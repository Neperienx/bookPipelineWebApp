from flask_wtf import FlaskForm
from wtforms import SubmitField, TextAreaField, StringField
from wtforms.validators import InputRequired, Length


class ProjectForm(FlaskForm):
    title = StringField("Project title", validators=[InputRequired(), Length(max=150)])
    description = TextAreaField("Short description", validators=[Length(max=500)])
    submit = SubmitField("Create project")
