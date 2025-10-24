from flask import (
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import ActOutline, CharacterProfile, OutlineDraft, Project, ProjectStage
from ..services.autofill import (
    CharacterAutofillError,
    OutlineAutofillError,
    autofill_characters_for_project,
    autofill_outline_for_project,
)
from ..services.stage_generation import (
    StageGenerationError,
    generate_stage_content,
)
from ..services.story_outline import OutlineGenerationError, generate_story_outline
from . import bp
from .forms import ActOutlineForm, CharacterProfileForm, OutlineDraftForm, OutlineRequestForm


PROJECT_STEPS = [
    ("prompt", "Initial story prompt"),
    ("characters", "Character development"),
    ("three_act", "Three-act outline"),
    ("chapters", "Chapter outline"),
    ("scenes", "Scene outline"),
    ("manuscript", "Draft manuscript"),
]


STAGE_GENERATION_STEPS = {
    "prompt": {
        "label": "Initial story prompt",
        "cta": "Generate project brief",
        "description": "Capture the core hook, tone, and stakes to guide later stages.",
        "input_label": "Describe the spark",
        "input_placeholder": "Summarise the idea, genre, and any must-have beats.",
        "output_label": "Expanded project brief",
    },
    "characters": {
        "label": "Character development",
        "cta": "Outline character set",
        "description": "Sketch the protagonist, opposing force, and key allies.",
        "input_label": "What do you know about the cast?",
        "input_placeholder": "Share goals, flaws, relationships, or archetypes.",
        "output_label": "Suggested character breakdown",
    },
}


@bp.route("/<int:project_id>", methods=["GET", "POST"])
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

    outline_form = OutlineRequestForm(prefix="outline")
    outline_edit_form = OutlineDraftForm(prefix="edit_outline")
    act_form = ActOutlineForm(prefix="act")
    character_form = CharacterProfileForm(prefix="character")

    if request.method == "GET":
        outline_form.prompt.data = project.last_outline_prompt or ""

    outline_result = None
    if outline_form.submit.data and outline_form.validate_on_submit():
        saved_prompt = (outline_form.prompt.data or "").strip()
        normalized_prompt = saved_prompt or None
        commit_needed = False
        if project.last_outline_prompt != normalized_prompt:
            project.last_outline_prompt = normalized_prompt
            commit_needed = True
        try:
            outline_result = generate_story_outline(
                outline_form.prompt.data,
                project_title=project.title,
            )
            draft = OutlineDraft(
                project=project,
                title=f"Outline draft {len(project.outlines) + 1}",
                content=outline_result.outline,
                prompt=outline_result.prompt,
                word_count=outline_result.word_count,
                used_fallback=outline_result.used_fallback,
            )
            db.session.add(draft)
            flash("Draft outline created from your story seed.", "success")
        except OutlineGenerationError as exc:
            flash(str(exc), "danger")
        except Exception:  # pragma: no cover - defensive logging for unexpected states
            current_app.logger.exception("Unexpected error while generating story outline")
            flash("We couldn't generate an outline right now. Please try again.", "danger")
        finally:
            if commit_needed:
                db.session.commit()
            if outline_result:
                db.session.commit()
                return redirect(
                    url_for(
                        "projects.detail",
                        project_id=project.id,
                        outline_id=draft.id,
                    )
                )

    if outline_edit_form.submit.data and outline_edit_form.validate_on_submit():
        outline_id_raw = outline_edit_form.outline_id.data
        outline_id = int(outline_id_raw) if outline_id_raw else None
        if not outline_id:
            flash("Select an outline to update.", "warning")
        else:
            draft = OutlineDraft.query.filter_by(id=outline_id, project_id=project.id).first()
            if not draft:
                flash("We couldn't find the selected outline.", "danger")
            else:
                draft.title = outline_edit_form.title.data.strip()
                draft.content = outline_edit_form.content.data.strip()
                db.session.commit()
                flash("Outline saved.", "success")
                return redirect(
                    url_for(
                        "projects.detail",
                        project_id=project.id,
                        outline_id=draft.id,
                    )
                )

    if act_form.submit.data and act_form.validate_on_submit():
        act_id_raw = act_form.act_id.data
        act_id = int(act_id_raw) if act_id_raw else None
        if act_id:
            act = ActOutline.query.filter_by(id=act_id, project_id=project.id).first()
            if not act:
                flash("We couldn't find the selected act outline.", "danger")
            else:
                act.sequence = act_form.sequence.data or act.sequence
                act.title = act_form.title.data.strip()
                act.summary = act_form.summary.data.strip()
                act.turning_points = (act_form.turning_points.data or "").strip() or None
                act.notes = (act_form.notes.data or "").strip() or None
                db.session.commit()
                flash("Act outline updated.", "success")
                return redirect(
                    url_for(
                        "projects.detail",
                        project_id=project.id,
                        act_id=act.id,
                    )
                )
        else:
            sequence = act_form.sequence.data or 1
            act = ActOutline(
                project=project,
                sequence=sequence,
                title=act_form.title.data.strip(),
                summary=act_form.summary.data.strip(),
                turning_points=(act_form.turning_points.data or "").strip() or None,
                notes=(act_form.notes.data or "").strip() or None,
            )
            db.session.add(act)
            db.session.commit()
            flash("Act outline added.", "success")
            return redirect(
                url_for(
                    "projects.detail",
                    project_id=project.id,
                    act_id=act.id,
                )
            )

    if character_form.submit.data and character_form.validate_on_submit():
        character_id_raw = character_form.character_id.data
        character_id = int(character_id_raw) if character_id_raw else None
        if character_id:
            character = CharacterProfile.query.filter_by(
                id=character_id, project_id=project.id
            ).first()
            if not character:
                flash("We couldn't find the selected character.", "danger")
            else:
                character.name = character_form.name.data.strip()
                character.role = (character_form.role.data or "").strip() or None
                character.background = (character_form.background.data or "").strip() or None
                character.goals = (character_form.goals.data or "").strip() or None
                character.conflict = (character_form.conflict.data or "").strip() or None
                character.notes = (character_form.notes.data or "").strip() or None
                db.session.commit()
                flash("Character updated.", "success")
                return redirect(
                    url_for(
                        "projects.detail",
                        project_id=project.id,
                        character_id=character.id,
                    )
                )
        else:
            character = CharacterProfile(
                project=project,
                name=character_form.name.data.strip(),
                role=(character_form.role.data or "").strip() or None,
                background=(character_form.background.data or "").strip() or None,
                goals=(character_form.goals.data or "").strip() or None,
                conflict=(character_form.conflict.data or "").strip() or None,
                notes=(character_form.notes.data or "").strip() or None,
            )
            db.session.add(character)
            db.session.commit()
            flash("Character added to the project.", "success")
            return redirect(
                url_for(
                    "projects.detail",
                    project_id=project.id,
                    character_id=character.id,
                )
            )

    stage_entries = {
        entry.stage: entry
        for entry in ProjectStage.query.filter_by(project_id=project.id).all()
    }

    outlines = (
        OutlineDraft.query.filter_by(project_id=project.id)
        .order_by(OutlineDraft.created_at.desc())
        .all()
    )
    acts = (
        ActOutline.query.filter_by(project_id=project.id)
        .order_by(ActOutline.sequence.asc(), ActOutline.created_at.asc())
        .all()
    )
    characters = (
        CharacterProfile.query.filter_by(project_id=project.id)
        .order_by(CharacterProfile.name.asc())
        .all()
    )

    selected_outline = None
    selected_act = None
    selected_character = None

    outline_query_id = request.args.get("outline_id", type=int)
    outline_form_id = None
    if not outline_query_id and outline_edit_form.outline_id.data:
        try:
            outline_form_id = int(outline_edit_form.outline_id.data)
        except (TypeError, ValueError):
            outline_form_id = None
    target_outline_id = outline_query_id or outline_form_id
    if target_outline_id:
        selected_outline = next((o for o in outlines if o.id == target_outline_id), None)
    if not selected_outline and outlines:
        selected_outline = outlines[0]

    if selected_outline and (request.method == "GET" or not outline_edit_form.submit.data):
        outline_edit_form.outline_id.data = str(selected_outline.id)
        outline_edit_form.title.data = selected_outline.title
        outline_edit_form.content.data = selected_outline.content

    act_query_id = request.args.get("act_id")
    if act_query_id and act_query_id != "new":
        try:
            act_id = int(act_query_id)
        except ValueError:
            act_id = None
        if act_id:
            selected_act = next((a for a in acts if a.id == act_id), None)
    if not selected_act and act_form.act_id.data and act_form.act_id.data.isdigit():
        act_id_from_form = int(act_form.act_id.data)
        selected_act = next((a for a in acts if a.id == act_id_from_form), None)
    if act_query_id != "new" and not selected_act and acts:
        selected_act = acts[0]

    if selected_act and (request.method == "GET" or act_query_id == "new" or not act_form.submit.data):
        act_form.act_id.data = str(selected_act.id)
        act_form.sequence.data = selected_act.sequence
        act_form.title.data = selected_act.title
        act_form.summary.data = selected_act.summary
        act_form.turning_points.data = selected_act.turning_points or ""
        act_form.notes.data = selected_act.notes or ""
    elif act_query_id == "new" and (request.method == "GET" or not act_form.submit.data):
        act_form.act_id.data = ""
        act_form.sequence.data = len(acts) + 1
        act_form.title.data = ""
        act_form.summary.data = ""
        act_form.turning_points.data = ""
        act_form.notes.data = ""

    character_query_id = request.args.get("character_id")
    if character_query_id and character_query_id != "new":
        try:
            character_id = int(character_query_id)
        except ValueError:
            character_id = None
        if character_id:
            selected_character = next((c for c in characters if c.id == character_id), None)
    if not selected_character and character_form.character_id.data and character_form.character_id.data.isdigit():
        character_id_from_form = int(character_form.character_id.data)
        selected_character = next((c for c in characters if c.id == character_id_from_form), None)
    if character_query_id != "new" and not selected_character and characters:
        selected_character = characters[0]

    if selected_character and (
        request.method == "GET" or character_query_id == "new" or not character_form.submit.data
    ):
        character_form.character_id.data = str(selected_character.id)
        character_form.name.data = selected_character.name
        character_form.role.data = selected_character.role or ""
        character_form.background.data = selected_character.background or ""
        character_form.goals.data = selected_character.goals or ""
        character_form.conflict.data = selected_character.conflict or ""
        character_form.notes.data = selected_character.notes or ""
    elif character_query_id == "new" and (request.method == "GET" or not character_form.submit.data):
        character_form.character_id.data = ""
        character_form.name.data = ""
        character_form.role.data = ""
        character_form.background.data = ""
        character_form.goals.data = ""
        character_form.conflict.data = ""
        character_form.notes.data = ""

    base_query_args = {}
    if selected_outline:
        base_query_args["outline_id"] = selected_outline.id
    if selected_act:
        base_query_args["act_id"] = selected_act.id
    if selected_character:
        base_query_args["character_id"] = selected_character.id

    def build_project_url(**overrides: object) -> str:
        params = {**base_query_args, **overrides}
        filtered_params = {
            key: value
            for key, value in params.items()
            if value not in (None, "")
        }
        return url_for("projects.detail", project_id=project.id, **filtered_params)

    stage_detail_links = {
        "prompt": f"{build_project_url()}#outlineLibrary",
        "characters": f"{build_project_url()}#characterLibrary",
    }

    stage_quick_actions = {
        "prompt": {
            "label": "Refine outline",
            "url": f"{build_project_url()}#outlineForm",
        },
        "characters": {
            "label": "Add character",
            "url": f"{build_project_url(character_id='new')}#characterLibrary",
        },
    }

    return render_template(
        "projects/project_detail.html",
        project=project,
        steps=PROJECT_STEPS,
        current_index=current_index,
        outline_form=outline_form,
        outline_result=outline_result,
        outline_edit_form=outline_edit_form,
        act_form=act_form,
        character_form=character_form,
        outlines=outlines,
        acts=acts,
        characters=characters,
        selected_outline=selected_outline,
        selected_act=selected_act,
        selected_character=selected_character,
        stage_entries=stage_entries,
        stage_generation_steps=STAGE_GENERATION_STEPS,
        stage_detail_links=stage_detail_links,
        stage_quick_actions=stage_quick_actions,
    )


@bp.route("/<int:project_id>/stage-content", methods=["POST"])
@login_required
def generate_stage(project_id: int):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        abort(403)

    payload = request.get_json(silent=True) or {}
    stage = payload.get("stage")
    prompt = payload.get("prompt", "")

    if stage not in STAGE_GENERATION_STEPS:
        return jsonify({"error": "Unknown stage requested."}), 400

    try:
        result = generate_stage_content(stage, prompt, project_title=project.title)
    except StageGenerationError as exc:
        return jsonify({"error": str(exc)}), 400

    entry = ProjectStage.query.filter_by(project_id=project.id, stage=stage).first()
    if not entry:
        entry = ProjectStage(project=project, stage=stage)

    entry.user_prompt = (prompt or "").strip() or None
    entry.generated_text = result.text
    entry.used_fallback = result.used_fallback
    db.session.add(entry)
    db.session.flush()

    response_payload = {
        "stage": stage,
        "label": STAGE_GENERATION_STEPS[stage]["label"],
        "content": entry.generated_text,
        "used_fallback": entry.used_fallback,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }

    if stage == "prompt":
        try:
            outline_autofill = autofill_outline_for_project(project, prompt)
        except OutlineAutofillError as exc:
            current_app.logger.warning("Outline autofill failed: %s", exc)
        else:
            response_payload["outline"] = {
                "id": outline_autofill.draft.id,
                "title": outline_autofill.draft.title,
                "word_count": outline_autofill.word_count,
                "used_fallback": outline_autofill.used_fallback,
            }
    elif stage == "characters":
        try:
            character_autofill = autofill_characters_for_project(project, prompt)
        except CharacterAutofillError as exc:
            current_app.logger.warning("Character autofill failed: %s", exc)
        else:
            response_payload["characters"] = {
                "created_ids": [character.id for character in character_autofill.created],
                "updated_ids": [character.id for character in character_autofill.updated],
                "used_fallback": character_autofill.used_fallback,
            }

    db.session.commit()

    return jsonify(response_payload)


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
