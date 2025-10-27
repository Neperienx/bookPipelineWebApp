from flask_wtf import FlaskForm
from wtforms import HiddenField, IntegerField, StringField, SubmitField, TextAreaField
from wtforms.validators import InputRequired, Length, Optional, NumberRange


class ProjectForm(FlaskForm):
    title = StringField("Project title", validators=[InputRequired(), Length(max=150)])
    description = TextAreaField("Short description", validators=[Length(max=500)])
    submit = SubmitField("Create project")


class OutlineRequestForm(FlaskForm):
    prompt = TextAreaField(
        "Story seed",
        validators=[InputRequired(), Length(min=10, max=2000)],
    )
    submit = SubmitField("Generate outline")


class OutlineDraftForm(FlaskForm):
    outline_id = HiddenField(validators=[Optional()])
    title = StringField("Outline title", validators=[InputRequired(), Length(max=150)])
    content = TextAreaField("Outline content", validators=[InputRequired()])
    submit = SubmitField("Save outline")


class ActOutlineForm(FlaskForm):
    act_id = HiddenField(validators=[Optional()])
    sequence = IntegerField(
        "Act number",
        validators=[Optional(), NumberRange(min=1, max=20)],
        description="Order of this act in your structure",
    )
    title = StringField("Act title", validators=[InputRequired(), Length(max=150)])
    summary = TextAreaField("Act summary", validators=[InputRequired()])
    turning_points = TextAreaField("Turning points", validators=[Optional()])
    notes = TextAreaField("Additional notes", validators=[Optional()])
    submit = SubmitField("Save act outline")


class CharacterProfileForm(FlaskForm):
    character_id = HiddenField(validators=[Optional()])
    name = StringField("Name", validators=[InputRequired(), Length(max=120)])
    role = StringField("Story role", validators=[Optional(), Length(max=120)])
    background = TextAreaField("Background", validators=[Optional()])
    goals = TextAreaField("Goals & desires", validators=[Optional()])
    conflict = TextAreaField("Conflicts & obstacles", validators=[Optional()])
    notes = TextAreaField("Additional notes", validators=[Optional()])
    seed_prompt = TextAreaField("Idea prompt", validators=[Optional(), Length(max=2000)])
    submit = SubmitField("Save character")
