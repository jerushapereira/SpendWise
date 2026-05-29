import csv
import io
import json
import os
import sqlite3
from calendar import monthrange
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import requests
except ImportError:  # Local insights keep the app usable before requirements are installed.
    requests = None

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
except ImportError:  # The app still runs; requirements.txt includes reportlab for PDF export.
    SimpleDocTemplate = None


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, "database", "expense_tracker.db")

CATEGORIES = [
    "Food",
    "Transport",
    "Shopping",
    "Bills",
    "Entertainment",
    "Education",
    "Health",
    "Other",
]
PAYMENT_METHODS = ["Cash", "Debit Card", "Credit Card", "UPI", "Bank Transfer", "Wallet", "Other"]
INCOME_CATEGORIES = ["Salary", "Freelance", "Allowance", "Scholarship", "Investment", "Gift", "Other"]


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-this-for-production")


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.join(BASE_DIR, "database"), exist_ok=True)
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                theme TEXT NOT NULL DEFAULT 'light',
                currency TEXT NOT NULL DEFAULT '₹',
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('expense', 'income')),
                amount REAL NOT NULL CHECK(amount > 0),
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                payment_method TEXT,
                transaction_date TEXT NOT NULL,
                notes TEXT,
                recurring_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                monthly_limit REAL NOT NULL CHECK(monthly_limit >= 0),
                UNIQUE(user_id, category),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS savings_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                target_amount REAL NOT NULL CHECK(target_amount > 0),
                current_amount REAL NOT NULL DEFAULT 0 CHECK(current_amount >= 0),
                due_date TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS recurring_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL CHECK(amount > 0),
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                payment_method TEXT,
                day_of_month INTEGER NOT NULL CHECK(day_of_month BETWEEN 1 AND 31),
                start_date TEXT NOT NULL,
                last_generated_month TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def current_user():
    if "user_id" not in session:
        return None
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()


def get_settings(user_id):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_settings (user_id, theme, currency) VALUES (?, 'light', '₹')",
            (user_id,),
        )
        return conn.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()


@app.context_processor
def inject_globals():
    user = current_user()
    settings = get_settings(user["id"]) if user else None
    return {
        "current_user": user,
        "settings": settings,
        "categories": CATEGORIES,
        "payment_methods": PAYMENT_METHODS,
        "income_categories": INCOME_CATEGORIES,
        "today": date.today().isoformat(),
    }


def parse_amount(value):
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def parse_date(value):
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return parsed.isoformat()


def month_bounds(target=None):
    target = target or date.today()
    start = target.replace(day=1)
    end = target.replace(day=monthrange(target.year, target.month)[1])
    return start.isoformat(), end.isoformat(), target.strftime("%Y-%m")


def apply_recurring_expenses(user_id):
    today_value = date.today()
    month_key = today_value.strftime("%Y-%m")
    _, _, current_month = month_bounds(today_value)
    with get_db() as conn:
        recurring = conn.execute(
            """
            SELECT * FROM recurring_expenses
            WHERE user_id = ? AND is_active = 1 AND start_date <= ?
            """,
            (user_id, today_value.isoformat()),
        ).fetchall()

        for item in recurring:
            if item["last_generated_month"] == current_month:
                continue
            last_day = monthrange(today_value.year, today_value.month)[1]
            tx_day = min(item["day_of_month"], last_day)
            tx_date = today_value.replace(day=tx_day).isoformat()
            conn.execute(
                """
                INSERT INTO transactions
                (user_id, type, amount, category, description, payment_method, transaction_date, notes, recurring_id)
                VALUES (?, 'expense', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    item["amount"],
                    item["category"],
                    item["description"],
                    item["payment_method"],
                    tx_date,
                    "Auto-generated recurring expense",
                    item["id"],
                ),
            )
            conn.execute(
                "UPDATE recurring_expenses SET last_generated_month = ? WHERE id = ?",
                (month_key, item["id"]),
            )


def dashboard_data(user_id):
    start, end, _ = month_bounds()
    with get_db() as conn:
        totals = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN type='expense' THEN amount END), 0) AS expenses,
              COALESCE(SUM(CASE WHEN type='income' THEN amount END), 0) AS income
            FROM transactions WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        monthly_spending = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE user_id = ? AND type = 'expense' AND transaction_date BETWEEN ? AND ?
            """,
            (user_id, start, end),
        ).fetchone()["total"]
        recent = conn.execute(
            """
            SELECT * FROM transactions
            WHERE user_id = ?
            ORDER BY transaction_date DESC, id DESC
            LIMIT 6
            """,
            (user_id,),
        ).fetchall()
        budget_rows = conn.execute(
            """
            SELECT b.category, b.monthly_limit,
                   COALESCE(SUM(t.amount), 0) AS used
            FROM budgets b
            LEFT JOIN transactions t
              ON t.user_id = b.user_id
             AND t.category = b.category
             AND t.type = 'expense'
             AND t.transaction_date BETWEEN ? AND ?
            WHERE b.user_id = ? AND b.monthly_limit > 0
            GROUP BY b.id
            ORDER BY b.category
            """,
            (start, end, user_id),
        ).fetchall()
    return {
        "total_expenses": totals["expenses"],
        "total_income": totals["income"],
        "balance": totals["income"] - totals["expenses"],
        "monthly_spending": monthly_spending,
        "recent": recent,
        "budgets": budget_rows,
    }


def filtered_transactions(user_id, tx_type=None):
    filters = ["user_id = ?"]
    params = [user_id]
    if tx_type:
        filters.append("type = ?")
        params.append(tx_type)
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    sort = request.args.get("sort", "date_desc")

    if search:
        filters.append("(description LIKE ? OR notes LIKE ? OR payment_method LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if category:
        filters.append("category = ?")
        params.append(category)
    if start_date:
        filters.append("transaction_date >= ?")
        params.append(start_date)
    if end_date:
        filters.append("transaction_date <= ?")
        params.append(end_date)

    sort_sql = {
        "date_asc": "transaction_date ASC, id ASC",
        "amount_desc": "amount DESC",
        "amount_asc": "amount ASC",
    }.get(sort, "transaction_date DESC, id DESC")

    with get_db() as conn:
        return conn.execute(
            f"SELECT * FROM transactions WHERE {' AND '.join(filters)} ORDER BY {sort_sql}",
            params,
        ).fetchall()


def build_analytics(user_id):
    with get_db() as conn:
        category_rows = conn.execute(
            """
            SELECT category, COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE user_id = ? AND type = 'expense'
            GROUP BY category ORDER BY total DESC
            """,
            (user_id,),
        ).fetchall()
        monthly_rows = conn.execute(
            """
            SELECT strftime('%Y-%m', transaction_date) AS month,
                   COALESCE(SUM(CASE WHEN type='expense' THEN amount END), 0) AS expenses,
                   COALESCE(SUM(CASE WHEN type='income' THEN amount END), 0) AS income
            FROM transactions
            WHERE user_id = ?
            GROUP BY month ORDER BY month
            LIMIT 12
            """,
            (user_id,),
        ).fetchall()
        trend_rows = conn.execute(
            """
            SELECT transaction_date, COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE user_id = ? AND type = 'expense'
            GROUP BY transaction_date ORDER BY transaction_date
            LIMIT 45
            """,
            (user_id,),
        ).fetchall()
    return {
        "category_labels": [row["category"] for row in category_rows],
        "category_values": [round(row["total"], 2) for row in category_rows],
        "monthly_labels": [row["month"] for row in monthly_rows],
        "monthly_expenses": [round(row["expenses"], 2) for row in monthly_rows],
        "monthly_income": [round(row["income"], 2) for row in monthly_rows],
        "trend_labels": [row["transaction_date"] for row in trend_rows],
        "trend_values": [round(row["total"], 2) for row in trend_rows],
    }


def local_insights(user_id):
    start, end, _ = month_bounds()
    with get_db() as conn:
        top_category = conn.execute(
            """
            SELECT category, SUM(amount) AS total
            FROM transactions
            WHERE user_id = ? AND type = 'expense' AND transaction_date BETWEEN ? AND ?
            GROUP BY category ORDER BY total DESC LIMIT 1
            """,
            (user_id, start, end),
        ).fetchone()
        weekends = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE user_id = ? AND type = 'expense' AND strftime('%w', transaction_date) IN ('0','6')
            """,
            (user_id,),
        ).fetchone()["total"]
        weekdays = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE user_id = ? AND type = 'expense' AND strftime('%w', transaction_date) NOT IN ('0','6')
            """,
            (user_id,),
        ).fetchone()["total"]
        budgets = conn.execute(
            """
            SELECT b.category, b.monthly_limit, COALESCE(SUM(t.amount), 0) AS used
            FROM budgets b
            LEFT JOIN transactions t ON t.user_id = b.user_id AND t.category = b.category
              AND t.type='expense' AND t.transaction_date BETWEEN ? AND ?
            WHERE b.user_id = ?
            GROUP BY b.id
            """,
            (start, end, user_id),
        ).fetchall()

    insights = []
    if top_category:
        insights.append(
            f"Your biggest expense category this month is {top_category['category']} at {top_category['total']:.2f}. Try setting a tighter weekly cap there."
        )
    if weekends > weekdays and weekends > 0:
        insights.append("Weekend spending is higher than weekday spending, so planning weekend meals or rides could improve savings.")
    for budget in budgets:
        if budget["monthly_limit"] and budget["used"] / budget["monthly_limit"] >= 0.85:
            insights.append(
                f"{budget['category']} is already at {budget['used'] / budget['monthly_limit']:.0%} of its monthly budget."
            )
    if not insights:
        insights.append("Add a few more transactions and budgets to unlock sharper spending patterns.")
    return insights[:5]


def summarize_for_ai(user_id):
    analytics = build_analytics(user_id)
    data = dashboard_data(user_id)
    return {
        "total_income": round(data["total_income"], 2),
        "total_expenses": round(data["total_expenses"], 2),
        "balance": round(data["balance"], 2),
        "monthly_spending": round(data["monthly_spending"], 2),
        "category_expenses": dict(zip(analytics["category_labels"], analytics["category_values"])),
        "monthly_expenses": dict(zip(analytics["monthly_labels"], analytics["monthly_expenses"])),
    }


@app.route("/")
def landing():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not name or not email or len(password) < 6:
            flash("Name, valid email, and a 6+ character password are required.", "danger")
            return render_template("signup.html")
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("signup.html")
        try:
            with get_db() as conn:
                cur = conn.execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                    (name, email, generate_password_hash(password)),
                )
                conn.execute("INSERT INTO user_settings (user_id) VALUES (?)", (cur.lastrowid,))
            flash("Account created. Welcome in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("An account with that email already exists.", "danger")
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            apply_recurring_expenses(user["id"])
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("landing"))


@app.route("/dashboard")
@login_required
def dashboard():
    apply_recurring_expenses(session["user_id"])
    data = dashboard_data(session["user_id"])
    return render_template("dashboard.html", **data)


@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    if request.method == "POST":
        amount = parse_amount(request.form.get("amount"))
        date_value = parse_date(request.form.get("transaction_date"))
        category = request.form.get("category", "")
        description = request.form.get("description", "").strip()
        if not amount or not date_value or category not in CATEGORIES or not description:
            flash("Please enter a valid expense amount, category, description, and date.", "danger")
        else:
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO transactions
                    (user_id, type, amount, category, description, payment_method, transaction_date, notes)
                    VALUES (?, 'expense', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["user_id"],
                        amount,
                        category,
                        description,
                        request.form.get("payment_method", "Other"),
                        date_value,
                        request.form.get("notes", "").strip(),
                    ),
                )
            flash("Expense added.", "success")
            return redirect(url_for("expenses"))
    return render_template("expenses.html", transactions=filtered_transactions(session["user_id"], "expense"))


@app.route("/income", methods=["GET", "POST"])
@login_required
def income():
    if request.method == "POST":
        amount = parse_amount(request.form.get("amount"))
        date_value = parse_date(request.form.get("transaction_date"))
        category = request.form.get("category", "Other")
        description = request.form.get("description", "").strip()
        if not amount or not date_value or not description:
            flash("Please enter a valid income amount, source, and date.", "danger")
        else:
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO transactions
                    (user_id, type, amount, category, description, payment_method, transaction_date, notes)
                    VALUES (?, 'income', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["user_id"],
                        amount,
                        category,
                        description,
                        request.form.get("payment_method", "Bank Transfer"),
                        date_value,
                        request.form.get("notes", "").strip(),
                    ),
                )
            flash("Income added.", "success")
            return redirect(url_for("income"))
    start, end, _ = month_bounds()
    with get_db() as conn:
        monthly_income = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE user_id = ? AND type='income' AND transaction_date BETWEEN ? AND ?
            """,
            (session["user_id"], start, end),
        ).fetchone()["total"]
    return render_template(
        "income.html",
        transactions=filtered_transactions(session["user_id"], "income"),
        monthly_income=monthly_income,
    )


@app.route("/transactions/<int:transaction_id>/edit", methods=["GET", "POST"])
@login_required
def edit_transaction(transaction_id):
    with get_db() as conn:
        tx = conn.execute(
            "SELECT * FROM transactions WHERE id = ? AND user_id = ?",
            (transaction_id, session["user_id"]),
        ).fetchone()
    if not tx:
        flash("Transaction not found.", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        amount = parse_amount(request.form.get("amount"))
        date_value = parse_date(request.form.get("transaction_date"))
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "Other")
        allowed_categories = CATEGORIES if tx["type"] == "expense" else INCOME_CATEGORIES
        if not amount or not date_value or not description or category not in allowed_categories:
            flash("Please fix the highlighted transaction details.", "danger")
        else:
            with get_db() as conn:
                conn.execute(
                    """
                    UPDATE transactions
                    SET amount=?, category=?, description=?, payment_method=?, transaction_date=?, notes=?
                    WHERE id=? AND user_id=?
                    """,
                    (
                        amount,
                        category,
                        description,
                        request.form.get("payment_method", "Other"),
                        date_value,
                        request.form.get("notes", "").strip(),
                        transaction_id,
                        session["user_id"],
                    ),
                )
            flash("Transaction updated.", "success")
            return redirect(url_for("expenses" if tx["type"] == "expense" else "income"))
    return render_template("transaction_form.html", tx=tx)


@app.route("/transactions/<int:transaction_id>/delete", methods=["POST"])
@login_required
def delete_transaction(transaction_id):
    with get_db() as conn:
        tx = conn.execute(
            "SELECT type FROM transactions WHERE id = ? AND user_id = ?",
            (transaction_id, session["user_id"]),
        ).fetchone()
        conn.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, session["user_id"]))
    flash("Transaction deleted.", "info")
    return redirect(url_for("expenses" if tx and tx["type"] == "expense" else "income"))


@app.route("/analytics")
@login_required
def analytics():
    return render_template("analytics.html", analytics=build_analytics(session["user_id"]))


@app.route("/api/analytics")
@login_required
def analytics_api():
    return jsonify(build_analytics(session["user_id"]))


@app.route("/api/ai-insights", methods=["POST"])
@login_required
def ai_insights_api():
    fallback = local_insights(session["user_id"])
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or requests is None:
        return jsonify({"source": "local", "insights": fallback})

    prompt = (
        "You are a practical financial coach for students and young professionals. "
        "Return 4 short, specific spending insights as a JSON array of strings. "
        "Use plain language and avoid generic advice. Data: "
        + json.dumps(summarize_for_ai(session["user_id"]))
    )
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "max_completion_tokens": 400,
            },
            timeout=18,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        try:
            insights = json.loads(content)
        except json.JSONDecodeError:
            insights = [line.strip("-• ").strip() for line in content.splitlines() if line.strip()]
        return jsonify({"source": "groq", "insights": insights[:5] or fallback})
    except requests.RequestException:
        return jsonify({"source": "local", "insights": fallback, "warning": "Groq request failed."})


@app.route("/savings", methods=["GET", "POST"])
@login_required
def savings():
    if request.method == "POST":
        target = parse_amount(request.form.get("target_amount"))
        current = float(request.form.get("current_amount") or 0)
        name = request.form.get("name", "").strip()
        if not name or not target or current < 0:
            flash("Goal name and a valid target amount are required.", "danger")
        else:
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO savings_goals (user_id, name, target_amount, current_amount, due_date, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["user_id"],
                        name,
                        target,
                        current,
                        parse_date(request.form.get("due_date")) if request.form.get("due_date") else None,
                        request.form.get("notes", "").strip(),
                    ),
                )
            flash("Savings goal created.", "success")
            return redirect(url_for("savings"))
    with get_db() as conn:
        goals = conn.execute(
            "SELECT * FROM savings_goals WHERE user_id = ? ORDER BY created_at DESC",
            (session["user_id"],),
        ).fetchall()
    return render_template("savings.html", goals=goals)


@app.route("/savings/<int:goal_id>/update", methods=["POST"])
@login_required
def update_goal(goal_id):
    current = float(request.form.get("current_amount") or 0)
    with get_db() as conn:
        conn.execute(
            "UPDATE savings_goals SET current_amount = ? WHERE id = ? AND user_id = ?",
            (max(current, 0), goal_id, session["user_id"]),
        )
    flash("Goal progress updated.", "success")
    return redirect(url_for("savings"))


@app.route("/savings/<int:goal_id>/delete", methods=["POST"])
@login_required
def delete_goal(goal_id):
    with get_db() as conn:
        conn.execute("DELETE FROM savings_goals WHERE id = ? AND user_id = ?", (goal_id, session["user_id"]))
    flash("Goal deleted.", "info")
    return redirect(url_for("savings"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action")
        with get_db() as conn:
            if action == "theme":
                theme = "dark" if request.form.get("theme") == "dark" else "light"
                conn.execute("UPDATE user_settings SET theme = ? WHERE user_id = ?", (theme, session["user_id"]))
                flash("Theme preference saved.", "success")
            elif action == "budget":
                for category in CATEGORIES:
                    amount = float(request.form.get(f"budget_{category}", 0) or 0)
                    conn.execute(
                        """
                        INSERT INTO budgets (user_id, category, monthly_limit)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id, category) DO UPDATE SET monthly_limit=excluded.monthly_limit
                        """,
                        (session["user_id"], category, max(amount, 0)),
                    )
                flash("Budgets updated.", "success")
            elif action == "recurring":
                amount = parse_amount(request.form.get("amount"))
                date_value = parse_date(request.form.get("start_date"))
                category = request.form.get("category")
                description = request.form.get("description", "").strip()
                day = int(request.form.get("day_of_month") or 1)
                if not amount or category not in CATEGORIES or not date_value or not description:
                    flash("Recurring expense needs a valid amount, category, date, and description.", "danger")
                else:
                    conn.execute(
                        """
                        INSERT INTO recurring_expenses
                        (user_id, amount, category, description, payment_method, day_of_month, start_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session["user_id"],
                            amount,
                            category,
                            description,
                            request.form.get("payment_method", "Other"),
                            min(max(day, 1), 31),
                            date_value,
                        ),
                    )
                    flash("Recurring expense added.", "success")
        return redirect(url_for("settings"))

    with get_db() as conn:
        budgets = conn.execute("SELECT * FROM budgets WHERE user_id = ?", (session["user_id"],)).fetchall()
        recurring = conn.execute(
            "SELECT * FROM recurring_expenses WHERE user_id = ? ORDER BY is_active DESC, day_of_month",
            (session["user_id"],),
        ).fetchall()
    budget_map = {row["category"]: row["monthly_limit"] for row in budgets}
    return render_template("settings.html", budget_map=budget_map, recurring=recurring)


@app.route("/recurring/<int:recurring_id>/delete", methods=["POST"])
@login_required
def delete_recurring(recurring_id):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM recurring_expenses WHERE id = ? AND user_id = ?",
            (recurring_id, session["user_id"]),
        )
    flash("Recurring expense removed.", "info")
    return redirect(url_for("settings"))


@app.route("/export/csv")
@login_required
def export_csv():
    rows = filtered_transactions(session["user_id"])
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Type", "Date", "Category", "Description", "Amount", "Payment Method", "Notes"])
    for row in rows:
        writer.writerow(
            [
                row["type"],
                row["transaction_date"],
                row["category"],
                row["description"],
                row["amount"],
                row["payment_method"],
                row["notes"],
            ]
        )
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


@app.route("/export/pdf")
@login_required
def export_pdf():
    if SimpleDocTemplate is None:
        flash("PDF export requires reportlab. Run pip install -r requirements.txt.", "danger")
        return redirect(url_for("settings"))

    rows = filtered_transactions(session["user_id"])
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("Expense Tracker Report", styles["Title"]),
        Paragraph(f"Generated on {date.today().isoformat()}", styles["Normal"]),
        Spacer(1, 12),
    ]
    table_data = [["Date", "Type", "Category", "Description", "Amount"]]
    for row in rows[:80]:
        table_data.append(
            [
                row["transaction_date"],
                row["type"].title(),
                row["category"],
                row["description"][:30],
                f"{row['amount']:.2f}",
            ]
        )
    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#34150F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D39858")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=expense_report.pdf"},
    )


init_db()


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
