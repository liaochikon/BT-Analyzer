"""Global node/button theme template DATA (not logic).

These were ~70 lines of pure declarations sitting inside the ``BaseNode`` class body, mixed in with
its behaviour. Moved here so tweaking a colour (e.g. a pin hue) is a data edit in one obvious place,
and ``BaseNode`` reads as behaviour only. ``BaseNode.build_global_themes`` consumes these lists;
they are assigned onto ``BaseNode`` in base_node.py so the classmethod's ``cls.<...>_templates``
lookup is unchanged.
"""

import palette
from core.theme import ButtonThemeTemplate, NodeThemeTemplate

# Node-frame alpha for the semantic (error/warning) title bars + outlines. node_default's blue is
# decorative (not a semantic state), so it stays a literal here; only error/warning reference the
# shared palette (see palette.py). Frames use alpha 200.
_A = 200

GLOBAL_NODE_THEME_TEMPLATES = [
    NodeThemeTemplate(
        theme_name="node_default",
        title_bar_color=[70, 130, 180, 200],
        title_bar_hover_color=[100, 160, 210, 200],
        title_bar_selected_color=[100, 160, 210, 200],
        outline_color=[50, 100, 150, 200]),
    NodeThemeTemplate(
        theme_name="node_error",
        title_bar_color=palette.rgba(palette.ERROR_FILL, _A),
        title_bar_hover_color=palette.rgba(palette.ERROR_TEXT, _A),
        title_bar_selected_color=palette.rgba(palette.ERROR_TEXT, _A),
        outline_color=palette.rgba(palette.ERROR_EDGE, _A)),
    NodeThemeTemplate(
        theme_name="node_warning",
        title_bar_color=palette.rgba(palette.WARN, _A),
        title_bar_hover_color=palette.rgba(palette.WARN_HI, _A),
        title_bar_selected_color=palette.rgba(palette.WARN_HI, _A),
        outline_color=palette.rgba(palette.WARN_EDGE, _A)),
]

GLOBAL_BUTTON_THEME_TEMPLATES = [
    ButtonThemeTemplate(
        theme_name="bake_btn",
        base_color=[90,  150, 210, 100],
        hover_color=[90,  150, 210, 200],
        active_color=[40,  100, 160, 200],
        rounding=4, padding=(6, 3)),
    ButtonThemeTemplate(
        theme_name="panel_btn",
        base_color=[90,  190, 130, 100],
        hover_color=[90,  190, 130, 200],
        active_color=[40,  140,  80, 200],
        rounding=4, padding=(6, 3)),
    ButtonThemeTemplate(
        theme_name="default_input_pin",
        base_color=[105, 130, 155, 200],
        hover_color=[105, 130, 155, 200],
        active_color=[105, 130, 155, 200],
        rounding=4, padding=(6, 3)),
    ButtonThemeTemplate(
        theme_name="default_output_pin",
        base_color=[120, 150, 120, 200],
        hover_color=[120, 150, 120, 200],
        active_color=[120, 150, 120, 200],
        rounding=4, padding=(6, 3)),
    # Per-DataType pin colours: one hue per pin type so the type is readable at a glance
    # (input vs output is already clear from position). Hues are the data-viz categorical
    # palette's dark-surface steps, in the order that maximises colour-vision-deficiency
    # separation; identity never rests on colour alone (every pin also has a text label).
    # _draw_pin looks these up by the pin's DataType name -> "pin_<TYPE>".
    ButtonThemeTemplate(theme_name="pin_AUDIO", base_color=[57, 135, 229, 210],
                        hover_color=[57, 135, 229, 210], active_color=[57, 135, 229, 210],
                        rounding=4, padding=(6, 3)),
    ButtonThemeTemplate(theme_name="pin_MIDI", base_color=[25, 158, 112, 210],
                        hover_color=[25, 158, 112, 210], active_color=[25, 158, 112, 210],
                        rounding=4, padding=(6, 3)),
    ButtonThemeTemplate(theme_name="pin_TEXT", base_color=[201, 133, 0, 210],
                        hover_color=[201, 133, 0, 210], active_color=[201, 133, 0, 210],
                        rounding=4, padding=(6, 3)),
    ButtonThemeTemplate(theme_name="pin_TEMPO", base_color=[144, 133, 233, 210],
                        hover_color=[144, 133, 233, 210], active_color=[144, 133, 233, 210],
                        rounding=4, padding=(6, 3)),
    ButtonThemeTemplate(theme_name="pin_TIME", base_color=[230, 103, 103, 210],
                        hover_color=[230, 103, 103, 210], active_color=[230, 103, 103, 210],
                        rounding=4, padding=(6, 3)),
    ButtonThemeTemplate(theme_name="pin_PITCH", base_color=[213, 81, 129, 210],
                        hover_color=[213, 81, 129, 210], active_color=[213, 81, 129, 210],
                        rounding=4, padding=(6, 3)),
    ButtonThemeTemplate(theme_name="pin_VOL", base_color=[217, 89, 38, 210],
                        hover_color=[217, 89, 38, 210], active_color=[217, 89, 38, 210],
                        rounding=4, padding=(6, 3)),
]
