from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField
from wtforms.validators import InputRequired, Length


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
