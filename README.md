# SpendWise Expense Tracker

A Flask + SQLite expense tracker built for students and young professionals. It includes authentication, expense and income tracking, budgets, savings goals, recurring expenses, analytics with Chart.js, CSV/PDF export, dark mode, and Groq-powered AI spending insights.

## Tech Stack

- Frontend: HTML, CSS, JavaScript
- Backend: Flask
- Database: SQLite
- Charts: Chart.js
- AI insights: Groq API with local fallback insights
- Auth: Flask sessions with hashed passwords

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

If port 5000 is busy, run the included development helper:

```powershell
.\.venv\Scripts\python.exe run_dev.py
```

Then open `http://127.0.0.1:5002`.

## Groq AI Insights

Set `GROQ_API_KEY` before starting the server to enable live AI insights:

```powershell
$env:GROQ_API_KEY="your_api_key_here"
python app.py
```

If no key is set, the app still works and shows rule-based local insights.

## Main Features

- Signup, login, logout, password hashing, protected routes
- Dashboard totals, monthly spending, recent transactions, budget progress
- Add, edit, delete, search, filter, and sort expenses and income
- Monthly category budgets with progress warnings
- Pie, bar, and line analytics charts
- Savings goals with percentage completion
- Recurring monthly expenses
- Dark/light mode saved per user
- CSV transaction export and PDF report export

SQLite data is created automatically in `database/expense_tracker.db` when the app starts.
