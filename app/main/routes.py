from flask import redirect, render_template, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Project
from ..projects.forms import ProjectForm
from . import bp


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("main/landing.html")


@bp.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    form = ProjectForm()
    if form.validate_on_submit():
        project = Project(
            title=form.title.data.strip(),
            description=(form.description.data or "").strip() or None,
            owner=current_user,
        )
        db.session.add(project)
        db.session.commit()
        return redirect(url_for("projects.detail", project_id=project.id))

    projects = (
        Project.query.filter_by(owner_id=current_user.id)
        .order_by(Project.updated_at.desc())
        .all()
    )
    return render_template("main/dashboard.html", projects=projects, form=form)
