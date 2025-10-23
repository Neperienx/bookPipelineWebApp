# Book Pipeline Studio

Book Pipeline Studio is a Flask-based web application that orchestrates every stage of writing a book with the help of a local language model. The platform is designed to guide authors from the initial story prompt all the way to a completed manuscript using a modular, extensible workflow. This repository currently focuses on the foundation: account management, project dashboards, and an immersive project workspace that will later host the AI-assisted chat experience.

---

## 1. Vision & Roadmap

| Stage | Milestone | Description |
| ----- | --------- | ----------- |
| ✅ Foundation | **Authentication** | Email-based registration, login, and logout using secure password hashing. |
| ✅ Foundation | **Project hub** | Dashboard for managing multiple story projects per user, with beautiful UI scaffolding. |
| ✅ Foundation | **Workspace shell** | Project-specific chat surface, status timeline, and actions to simulate progress through the pipeline. |
| 🔄 Next | **LLM integration** | Connect the local LLM endpoint for prompt completion, outline generation, and scene drafting. |
| 🔄 Next | **Persistence upgrades** | Track step-by-step artefacts (prompts, outlines, scenes) per project with version history. |
| 🔄 Next | **Collaboration tools** | Shared projects, comments, and export formats for finished manuscripts. |

The end goal is a modular pipeline that can evolve as we learn which creative steps produce the best results. Each milestone will remain loosely coupled so that we can reorder, iterate, or replace steps without rewriting the entire app.

---

## 2. Technology Stack

- **Runtime**: Python 3.13.7
- **Web framework**: Flask 3
- **Database**: SQLAlchemy with SQLite (development) and Flask-Migrate for future migrations
- **Forms & validation**: Flask-WTF and WTForms
- **Authentication**: Flask-Login with hashed passwords
- **Styling**: Bootstrap 5, custom gradients, and glassmorphism-inspired CSS
- **Local LLM**: Placeholder hooks for future integration with the provided on-premise model

---

## 3. Project Structure

```
bookPipelineWebApp/
├── app/
│   ├── __init__.py          # Flask application factory
│   ├── config.py            # Environment-aware configuration
│   ├── extensions.py        # SQLAlchemy, Migrate, LoginManager, CSRF
│   ├── models.py            # User and Project models
│   ├── auth/                # Authentication blueprint
│   │   ├── __init__.py
│   │   ├── forms.py
│   │   └── routes.py
│   ├── main/                # Landing page & dashboard blueprint
│   │   ├── __init__.py
│   │   └── routes.py
│   ├── projects/            # Project workspace blueprint
│   │   ├── __init__.py
│   │   ├── forms.py
│   │   └── routes.py
│   └── templates/           # Shared Jinja templates (base, auth, main, projects)
├── static/
│   └── css/styles.css       # Custom visual design
├── instance/                # Created on first run; holds SQLite database
├── requirements.txt         # Python dependencies
├── text_generator.py        # Existing script for experimenting with LLM outputs
└── wsgi.py                  # App entry point (for flask run / gunicorn)
```

---

## 4. Setup Instructions (Development)

> **Prerequisite:** Python 3.13.7 (the project uses features from the latest Python release).

1. **Clone and enter the repository**
   ```bash
   git clone <repo-url>
   cd bookPipelineWebApp
   ```

2. **Create and activate a virtual environment**
   ```bash
   python3.13 -m venv .venv
   source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set environment variables** (optional but recommended)
   ```bash
   export FLASK_APP=wsgi.py
   export FLASK_ENV=development
   export SECRET_KEY="replace-me"
   # If you already know your local LLM endpoint, add:
   export LLM_API_BASE="http://localhost:8000"
   ```

5. **Initialize the database**
   ```bash
   flask shell <<'PY'
   from app import create_app, db
   app = create_app()
   with app.app_context():
       db.create_all()
   PY
   ```
   This command creates an `instance/book_pipeline.db` SQLite database with the `users` and `projects` tables.

6. **Run the development server**
   ```bash
   flask run --debug
   ```
   The site is available at [http://localhost:5000](http://localhost:5000).

7. **Create your first account**
   - Navigate to `/register` and sign up.
   - The dashboard lets you create projects and open the immersive workspace for each one.

---

## 5. Application Walkthrough

### 5.1 Landing Page
- Hero section introduces the pipeline and highlights the connection to a local LLM.
- Roadmap cards explain how the story will move from idea to manuscript.
- CTA buttons invite visitors to register or scroll for details.

### 5.2 Authentication
- Email + password login with hashed credentials (`werkzeug.security`).
- CSRF protection is enabled application-wide.
- Flash messaging uses elegant toasts for feedback.

### 5.3 Dashboard
- Left side encourages new project creation with a concise form.
- Right side features a responsive grid of project cards, each with status badges and quick links to the workspace.
- Projects default to `draft` status and the first pipeline step (`prompt`).

### 5.4 Project Workspace
- Status timeline visualizes the full pipeline (prompt → characters → three-act outline → chapters → scenes → manuscript).
- Buttons allow you to simulate progress (`Advance step`) or reset to the beginning while persistence logic is built.
- The chat surface is styled for the upcoming conversational UX. For now it explains what will happen once the local LLM is wired in.

---

## 6. Connecting the Local LLM (Preview)

We plan to reuse the existing `text_generator.py` module as a staging point for local inference. The future integration will work roughly as follows:

1. **Expose your local LLM API** (HTTP endpoint or CLI wrapper) and note the base URL.
2. **Create a service layer** (e.g., `app/services/llm_client.py`) that wraps prompt/response calls and gracefully handles timeouts.
3. **Store generated artefacts** per project step (prompt, characters, outlines, scenes) so the user can revise or regenerate selectively.
4. **Stream updates into the chat UI** using Server-Sent Events or WebSockets for a real-time feel.

Until the service layer is implemented, the UI highlights that the assistant is in prototype mode.

---

## 7. Suggested Next Steps

1. **Model migrations**: Introduce Flask-Migrate commands and seed data for development demos.
2. **Project artefacts**: Create tables for prompts, outlines, and scenes linked to `Project` with revision history.
3. **LLM prompts**: Design prompt templates for each step (basic prompt, characters, three-act outline, etc.) and wire them into the chat workflow.
4. **Realtime UX**: Add websockets or SSE for incremental generation updates, plus editing tools for user revisions.
5. **Testing**: Configure pytest, factory-boy, and integration tests for authentication, project creation, and permission checks.

---

## 8. Design Language

- **Palette**: Neon gradients with deep navy backgrounds inspired by creative studios.
- **Typography**: Poppins for a modern and legible interface.
- **Components**: Glassmorphism cards, rounded buttons, and dynamic status pills reinforce a premium authoring experience.

Feel free to iterate on the styling in `static/css/styles.css` to match your brand.

---

## 9. Troubleshooting

| Issue | Fix |
| ----- | --- |
| *`ModuleNotFoundError: No module named 'flask'`* | Ensure the virtual environment is activated and `pip install -r requirements.txt` has completed. |
| *`sqlite3.OperationalError: unable to open database file`* | Confirm the `instance/` folder is writable. The app will create it automatically, but permissions may need adjusting on some systems. |
| *CSRF token missing* | When adding new forms, include `{{ form.hidden_tag() }}` for Flask-WTF forms or `{{ csrf_token() }}` for simple POST forms. |

---

## 10. Contributing

1. Fork the repository and branch off `work` (or your chosen base branch).
2. Follow the structure already established for blueprints, templates, and services.
3. Keep components modular so that new pipeline steps can be slotted in without major refactors.
4. Submit PRs with screenshots if you change the UI, and document any new dependencies in the README.

Happy building—and happy writing!
