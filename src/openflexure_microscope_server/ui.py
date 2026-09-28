"""Functionality for communicating the required user interface for a thing."""

from html import escape
from html.parser import HTMLParser
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, BeforeValidator, Field, RootModel

import labthings_fastapi as lt

# Only allowed tags are italic, bold, and link. Also allows self closing br tags.
ALLOWED_TAGS = {"i", "b", "a"}

ALLOWED_SCHEMES = {"http", "https"}


def strip_control_chars(input_string: str) -> str:
    """Remove unprintable characters."""
    return "".join(char for char in input_string if char.isprintable())


def sanitize_http_url(url: str) -> Optional[str]:
    """Santitise any url only allowing http and https without queries."""
    url = strip_control_chars(url)
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return None
    if not parsed.netloc:
        return None
    fragment_str = "#" + parsed.fragment if parsed.fragment else ""
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}{fragment_str}"


class SafeHTMLParser(HTMLParser):
    """An HTMLParser that only allows a very limited subset of HTML."""

    def __init__(self) -> None:
        """Initialise the parser."""
        super().__init__()
        self.output = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        """Append a start tag found in the HTML if it is in the allowed list.

        This is called by the base class when a start tag is found. We only append
        the tag if it is our allowed list. We re-write the tag so no extra attributes
        can be added.

        No tags can have attributes except for ``a`` tags,  why only allow ``href``. We then
        append ``target="_blank" rel="noopener noreferrer"`` to ensure the link opens
        in a new window.

        :param tag: the tag name
        :param attrs: the attributes for the tag.
        """
        if tag not in ALLOWED_TAGS:
            return

        if tag == "a":
            href = None
            for attr, value in attrs:
                if attr == "href" and value is not None:
                    # If it is not a httplink this will stay as None
                    href = sanitize_http_url(value.strip())

            if href:
                safe_href = escape(href, quote=True)
                # Always in a new tab
                self.output += (
                    f'<a href="{safe_href}" target="_blank" rel="noopener noreferrer">'
                )

            else:
                self.output += "<a>"
        else:
            self.output += f"<{tag}>"

    def handle_endtag(self, tag: str) -> None:
        """Append the end tag only if it is in the allowed list."""
        if tag in ALLOWED_TAGS:
            self.output += f"</{tag}>"

    def handle_startendtag(
        self, tag: str, _attrs: list[tuple[str, Optional[str]]]
    ) -> None:
        """Append a self-closing tag if it is a new line."""
        if tag == "br":
            self.output += "<br/>"

    def handle_data(self, data: str) -> None:
        """Append any data inside tags after escaping it."""
        self.output += escape(data)

    def handle_entityref(self, name: str) -> None:
        """Append any named character."""
        self.output += f"&{name};"

    def handle_charref(self, name: str) -> None:
        """Append any character references."""
        self.output += f"&#{name};"


def sanitise_html(html: str) -> str:
    """Santitise HTML to only have a small list of allowed tags.

    Tags allowed without attrs: ``<i>, <b>,``

    Tags allowed with attrs: ``<a>`` is allowed with ``href`` only. This automatically
    appends ``target="_blank" rel="noopener noreferrer"`` so the link opens externally.

    Self closing tags allowed ``<br>``.

    :param html: The input HTML.

    :return: The sanitised HTML.
    """
    parser = SafeHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.output


HtmlFragment = Annotated[str, BeforeValidator(sanitise_html)]


class HeaderBlock(BaseModel):
    """The data required for header."""

    element_type: Literal["header_block"] = "header_block"
    text: HtmlFragment
    """The header text (can include basic HTML tags)."""

    level: int = Field(default=2, ge=1, le=6)
    """The header level. A number from 1-6."""


class TextBlock(BaseModel):
    """The data required for creating a block text."""

    element_type: Literal["text_block"] = "text_block"
    text: HtmlFragment
    """The text (can include basic HTML tags)."""


class HelpBlock(BaseModel):
    """A compact, expandable explanation for an operator-facing interface."""

    element_type: Literal["help_block"] = "help_block"
    label: str
    text: Annotated[str, BeforeValidator(strip_control_chars)]


class BulletBlock(BaseModel):
    """The data required for creating a block text."""

    element_type: Literal["bullet_block"] = "bullet_block"
    bullets: list[HtmlFragment]
    """A list of text elements (can include basic HTML tags)."""


class ActionButton(BaseModel):
    """The data required for creating an actionButton in Vue.

    Currently this cannot be used to submitData, or to submitOnEvent.
    """

    element_type: Literal["action_button"] = "action_button"

    thing: str
    """The Thing "path" for the Thing instance."""

    action: str
    """The name of the action to be triggered."""

    poll_interval: float = 1
    """The interval for polling in seconds."""

    submit_label: str = "Submit"
    """The label for the action button."""

    can_terminate: bool = True
    """Specify whether the action can be terminated."""

    requires_confirmation: bool = False
    """Specify whether a confirmation modal is needed."""

    confirmation_message: str = "Start task?"
    """The message for the confirmation modal."""

    button_primary: bool = True
    """Specify whether the button is styled as a primary button."""

    disabled: bool = False
    """Specify whether the button is disabled."""

    modal_progress: bool = False
    """Specify whether to show a progress modal."""

    stream_with_modal: bool = False
    """Specify whether to show a mini stream preview in the progress modal.

    This option only has an effect if `modal_progress` is True.
    If `modal_progress` is False, no modal (and therefore no stream preview) is shown.
    """

    notify_on_success: bool = False
    """Specify whether to a notification on successful completion."""

    response_is_success_message: bool = False
    """Set True to use the action response as the success message.

    If ``success_message`` is set, this is never used.
    """

    success_message: str = "Success!"
    """The message to show on successful completion."""

    update_interface_on_response: bool = False
    """If True the action button emits a requestUpdate signal when completed.

    Downstream components can subscribe to this and request an update.
    """

    broken: bool = False
    """If True the action button is broken. There is no corresponding action."""


def action_button_for(thing: lt.Thing, action_name: str, **kwargs: Any) -> ActionButton:
    """Create a ActionButton data for the specified Thing Action.

    :param thing:  The instance of the thing that has the action.
    :param action_name: The name of the action to create a button for.
    :param kwargs: Any attribute of `ActionButton` except for ``thing`` or ``action``.
    :return: An ActionButton (Pydantic Model) object with all the information the
        webapp needs to create the action button.
    """
    if action_name not in thing.actions:
        thing.logger.error(f"{thing.name} has no action {action_name}")
        kwargs["broken"] = True
    return ActionButton(thing=thing.name, action=action_name, **kwargs)


class PropertyControl(BaseModel):
    """The data required for creating an actionButton in Vue.

    Currently this cannot be used to submitData, or to submitOnEvent.
    """

    element_type: Literal["property_control"] = "property_control"

    thing: str
    """The Thing "path" for the Thing instance."""

    property_name: str
    """The name of the property (or setting)."""

    label: str
    """The label to show in the UI"""

    read_back: bool = False
    """Whether or not to read back the property after setting.

    This is useful for hardware settings that may be coerced to the closest value.
    """

    read_back_delay: int = 1000
    """The delay in ms before reading back the property."""

    options: Optional[dict[str, str | int | float | bool]] = None
    """A mapping of UI display name to value, used for creating a dropdown.

    These options aren't validated here in any way. Any invalid values will be rejected
    when selected.
    """

    step: Optional[int | float] = None
    """The step size for a numerical input.

    If the property is not numeric this will be ignored.

    If this is left as None, then the UI will try to use the ``multipleOf`` field in
    the property dataSchema. If ``multipleOf`` is not set then the browser default is
    used.
    """

    broken: bool = False
    """If True the property control is broken. There is no corresponding property."""


def property_control_for(
    thing: lt.Thing, property_name: str, **kwargs: Any
) -> PropertyControl:
    """Create an PropertyControl data for the specified Thing Property.

    :param thing: The instance of the thing that has the property to be controlled.
    :param property_name: The name of the property to create a control for.
    :param kwargs: Any attribute of `PropertyControl` except for ``thing`` or
        ``property_name``. If label is not set here it will be the property name.
    """
    if "label" not in kwargs:
        kwargs["label"] = property_name
    if property_name not in thing.properties:
        thing.logger.error(f"{thing.name} has no property {property_name}")
        kwargs["broken"] = True
    return PropertyControl(thing=thing.name, property_name=property_name, **kwargs)


class Accordion(BaseModel):
    """The data required for creating an accordion in the UI.

    The HTML is limited to a small number of tags.
    """

    element_type: Literal["accordion"] = "accordion"
    title: str
    children: "UIElementList"


class Container(BaseModel):
    """The data required for creating ``<div>`` in the UI.

    The HTML is limited to a small number of tags.
    """

    element_type: Literal["container"] = "container"
    css_class: str
    children: "UIElementList"


UIElementModels = (
    HeaderBlock
    | TextBlock
    | HelpBlock
    | BulletBlock
    | PropertyControl
    | ActionButton
    | Accordion
    | Container
)


class UIElementList(RootModel[list[UIElementModels]]):
    """A list of user interface elements.

    Note! Two important things to consider:

    * This cannot be the return of a lt.property. To fully describe the UI recursion
        is used. Web of Things DataSchema doesn't support recursion. Use an
        lt.endpoint instead
    * If the module using this class imports future annotations then this will cause
        havoc with pydantics forward references.
    """


# Resolve forward references so Pydantic can build a sensible recursive schema
Accordion.model_rebuild()
Container.model_rebuild()
UIElementList.model_rebuild()

# This constant can be used as the value for the `responses` argument of lt.endpoint.
UI_ELEMENT_RESPONSE = {
    200: {
        "description": "A list of UI element specifications.",
        "model": UIElementList,
    }
}
