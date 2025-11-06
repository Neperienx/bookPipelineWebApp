from __future__ import annotations

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
from ..models import (
    ActOutline,
    CharacterProfile,
    ConceptDefinition,
    OutlineDraft,
    Project,
    ProjectStage,
)
from ..services.autofill import (
    CharacterProfileSuggestionError,
    draft_character_profile,
)
from ..services.concept_analysis import (
    ConceptClarificationError,
    clarify_outline_concepts,
)
from ..services.stage_generation import StageGenerationError, generate_stage_content
from ..services.story_outline import OutlineGenerationError, generate_story_outline
from . import bp
from .forms import ActOutlineForm, CharacterProfileForm, OutlineDraftForm, OutlineRequestForm


PROJECT_STEPS = [
    ("outline", "Outline"),
    ("characters", "Character roster"),
    ("act_outline", "Act outline"),
]


STAGE_GENERATION_STEPS = {
    "outline": {
        "label": "Outline",
        "description": "Build a high-level outline that captures the major beats.",
        "system_prompt": "You are an experienced narrative designer crafting crisp, structured outlines.",
    },
    "characters": {
        "label": "Create character",
        "description": "Develop a cast overview with motivations and conflicts.",
        "system_prompt": "You are a character development expert who finds vivid personalities in any idea.",
    },
    "act_outline": {
        "label": "Act outline",
        "description": "Transform the idea into a three-act progression with key turns.",
        "system_prompt": "You are a story architect specialising in clear, escalating act structures.",
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

    elif character_form.submit.data:
        handled_name_required = False
        for field_name, errors in character_form.errors.items():
            for error in errors:
                normalized = error.strip().lower()
                if (
                    field_name == "name"
                    and "required" in normalized
                    and not handled_name_required
                ):
                    flash("Add a character name before saving.", "danger")
                    handled_name_required = True
                else:
                    flash(error, "danger")

    stage_entries = {
        entry.stage: entry
        for entry in ProjectStage.query.filter_by(project_id=project.id).all()
    }

    stage_client_config = {
        stage_id: {
            "label": data.get("label", stage_id.replace("_", " ").title()),
            "description": data.get("description"),
            "system_prompt": data.get("system_prompt", ""),
        }
        for stage_id, data in STAGE_GENERATION_STEPS.items()
    }

    stage_client_entries = {
        stage_id: {
            "system_prompt": (entry.system_prompt or ""),
            "user_prompt": (entry.user_prompt or ""),
            "generated_text": (entry.generated_text or ""),
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
            "used_fallback": entry.used_fallback,
        }
        for stage_id, entry in stage_entries.items()
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
    concepts = (
        ConceptDefinition.query.filter_by(project_id=project.id)
        .order_by(ConceptDefinition.name.asc())
        .all()
    )

    concept_outline_id = concepts[0].outline_id if concepts else None
    concept_used_fallback = concepts[0].used_fallback if concepts else False
    concept_client_entries = [
        {
            "id": concept.id,
            "name": concept.name,
            "definition": concept.definition,
            "examples": concept.examples_list,
            "issue": concept.clarity_issue or "",
            "outline_id": concept.outline_id,
            "used_fallback": concept.used_fallback,
        }
        for concept in concepts
    ]

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
        character_form.seed_prompt.data = ""
    elif character_query_id == "new" and (request.method == "GET" or not character_form.submit.data):
        character_form.character_id.data = ""
        character_form.name.data = ""
        character_form.role.data = ""
        character_form.background.data = ""
        character_form.goals.data = ""
        character_form.conflict.data = ""
        character_form.notes.data = ""
        character_form.seed_prompt.data = ""

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
        concepts=concepts,
        selected_outline=selected_outline,
        selected_act=selected_act,
        selected_character=selected_character,
        stage_entries=stage_entries,
        stage_generation_steps=STAGE_GENERATION_STEPS,
        stage_client_config=stage_client_config,
        stage_client_entries=stage_client_entries,
        concept_outline_id=concept_outline_id,
        concept_client_entries=concept_client_entries,
        concept_used_fallback=concept_used_fallback,
    )


@bp.route("/<int:project_id>/concepts", methods=["POST"])
@login_required
def generate_concepts(project_id: int):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        abort(403)

    payload = request.get_json(silent=True) or {}
    outline_id_raw = payload.get("outline_id")
    try:
        outline_id = int(outline_id_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "Select a valid outline to analyse."}), 400

    outline = OutlineDraft.query.filter_by(id=outline_id, project_id=project.id).first()
    if not outline:
        return jsonify({"error": "We couldn't find the selected outline."}), 404

    outline_content = (outline.content or "").strip()
    if not outline_content:
        return jsonify({"error": "The selected outline is empty."}), 400

    try:
        result = clarify_outline_concepts(outline_content, project_title=project.title)
    except ConceptClarificationError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:  # pragma: no cover - defensive logging
        current_app.logger.exception("Unexpected error during concept clarification")
        return jsonify({"error": "We couldn't clarify concepts right now. Please try again."}), 500

    ConceptDefinition.query.filter_by(project_id=project.id).delete(synchronize_session=False)

    concept_models: list[ConceptDefinition] = []
    for concept in result.concepts:
        entry = ConceptDefinition(
            project=project,
            outline_id=outline.id,
            name=concept.name,
            clarity_issue=concept.issue or None,
            definition=concept.definition,
            examples="\n".join(concept.examples),
            used_fallback=result.used_fallback,
        )
        db.session.add(entry)
        concept_models.append(entry)

    db.session.commit()

    response_concepts = [
        {
            "id": concept.id,
            "name": concept.name,
            "definition": concept.definition,
            "examples": concept.examples_list,
            "issue": concept.clarity_issue or "",
            "outline_id": concept.outline_id,
            "used_fallback": concept.used_fallback,
        }
        for concept in concept_models
    ]

    return jsonify(
        {
            "concepts": response_concepts,
            "outline_id": outline.id,
            "outline_title": outline.title,
            "used_fallback": result.used_fallback,
        }
    )


@bp.route("/<int:project_id>/character-profile", methods=["POST"])
@login_required
def generate_character_profile(project_id: int):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        abort(403)

    payload = request.get_json(silent=True) or {}
    prompt_raw = payload.get("prompt", "")
    prompt = prompt_raw if isinstance(prompt_raw, str) else str(prompt_raw or "")

    try:
        result = draft_character_profile(prompt, project_title=project.title)
    except CharacterProfileSuggestionError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(
        {
            "profile": result.profile,
            "used_fallback": result.used_fallback,
            "prompt": result.prompt,
        }
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
    system_prompt = payload.get("system_prompt", "")

    if stage not in STAGE_GENERATION_STEPS:
        return jsonify({"error": "Unknown stage requested."}), 400

    stage_config = STAGE_GENERATION_STEPS[stage]
    default_system_prompt = (stage_config.get("system_prompt") or "").strip()
    effective_system_prompt = (system_prompt or "").strip() or default_system_prompt

    try:
        result = generate_stage_content(
            stage,
            prompt,
            system_prompt=effective_system_prompt,
            project_title=project.title,
        )
    except StageGenerationError as exc:
        return jsonify({"error": str(exc)}), 400

    entry = ProjectStage.query.filter_by(project_id=project.id, stage=stage).first()
    if not entry:
        entry = ProjectStage(project=project, stage=stage)

    entry.system_prompt = effective_system_prompt
    entry.user_prompt = (prompt or "").strip() or None
    entry.generated_text = result.text
    entry.used_fallback = result.used_fallback
    db.session.add(entry)
    db.session.flush()

    response_payload = {
        "stage": stage,
        "label": STAGE_GENERATION_STEPS[stage]["label"],
        "content": entry.generated_text,
        "system_prompt": entry.system_prompt,
        "used_fallback": entry.used_fallback,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
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
