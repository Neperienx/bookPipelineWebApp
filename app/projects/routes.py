from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Project
from . import bp


PROJECT_STEPS = [
    ("prompt", "Initial story prompt"),
    ("characters", "Character development"),
    ("three_act", "Three-act outline"),
    ("chapters", "Chapter outline"),
    ("scenes", "Scene outline"),
    ("manuscript", "Draft manuscript"),
]


@bp.route("/<int:project_id>")
@login_required
def detail(project_id: int):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        abort(403)

    step_ids = [step[0] for step in PROJECT_STEPS]
    try:
        current_index = step_ids.index(project.current_step)
    except ValueError:
        current_index = 0

    return render_template(
        "projects/project_detail.html",
        project=project,
        steps=PROJECT_STEPS,
        current_index=current_index,
    )


@bp.route("/<int:project_id>/advance", methods=["POST"])
@login_required
def advance(project_id: int):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        abort(403)

    current_index = next((i for i, step in enumerate(PROJECT_STEPS) if step[0] == project.current_step), 0)
    if current_index < len(PROJECT_STEPS) - 1:
        project.current_step = PROJECT_STEPS[current_index + 1][0]
        project.status = "in_progress" if project.current_step != PROJECT_STEPS[-1][0] else "complete"
        db.session.commit()
        flash("Project advanced to the next step.", "success")
    else:
        flash("This project is already complete.", "info")

    return redirect(url_for("projects.detail", project_id=project.id))


@bp.route("/<int:project_id>/reset", methods=["POST"])
@login_required
def reset(project_id: int):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        abort(403)

    project.current_step = PROJECT_STEPS[0][0]
    project.status = "draft"
    db.session.commit()
    flash("Project has been reset to the initial prompt stage.", "info")
    return redirect(url_for("projects.detail", project_id=project.id))
