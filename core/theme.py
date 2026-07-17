import dearpygui.dearpygui as dpg


class ButtonThemeTemplate:
    def __init__(self, theme_name=None,
                 base_color=None, hover_color=None, active_color=None,
                 rounding=None, padding=None,
                 text_color=None,
                 border_color=None, border_size=None,
                 text_align=None):
        self.theme_name = theme_name
        self.base_color = base_color
        self.hover_color = hover_color
        self.active_color = active_color
        self.rounding = rounding
        self.padding = padding
        self.text_color = text_color
        self.border_color = border_color
        self.border_size = border_size
        self.text_align = text_align

class NodeThemeTemplate:
    def __init__(self, theme_name=None,
                 title_bar_color=None, title_bar_hover_color=None, title_bar_selected_color=None,
                 outline_color=None,
                 background_color=None, background_hover_color=None, background_selected_color=None,
                 corner_rounding=None, padding=None, border_thickness=None):
        self.theme_name = theme_name
        self.title_bar_color = title_bar_color
        self.title_bar_hover_color = title_bar_hover_color
        self.title_bar_selected_color = title_bar_selected_color
        self.outline_color = outline_color
        self.background_color = background_color
        self.background_hover_color = background_hover_color
        self.background_selected_color = background_selected_color
        self.corner_rounding = corner_rounding
        self.padding = padding
        self.border_thickness = border_thickness

def init_btn_theme(button_theme: ButtonThemeTemplate):
    with dpg.theme() as ui_theme:
        with dpg.theme_component(dpg.mvButton):
            if button_theme.base_color is not None:
                dpg.add_theme_color(dpg.mvThemeCol_Button, button_theme.base_color)
            if button_theme.hover_color is not None:
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, button_theme.hover_color)
            if button_theme.active_color is not None:
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, button_theme.active_color)
            if button_theme.rounding is not None:
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, button_theme.rounding)
            if button_theme.padding is not None:
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, *button_theme.padding)
            if button_theme.text_color is not None:
                dpg.add_theme_color(dpg.mvThemeCol_Text, button_theme.text_color)
            if button_theme.border_color is not None:
                dpg.add_theme_color(dpg.mvThemeCol_Border, button_theme.border_color)
            if button_theme.border_size is not None:
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, button_theme.border_size)
            if button_theme.text_align is not None:
                dpg.add_theme_style(dpg.mvStyleVar_ButtonTextAlign, *button_theme.text_align)
    return ui_theme

def init_node_theme(node_theme: NodeThemeTemplate):
    with dpg.theme() as node_theme_ui:
        with dpg.theme_component(dpg.mvNode):
            if node_theme.title_bar_color is not None:
                dpg.add_theme_color(dpg.mvNodeCol_TitleBar, node_theme.title_bar_color, category=dpg.mvThemeCat_Nodes)
            if node_theme.title_bar_hover_color is not None:
                dpg.add_theme_color(dpg.mvNodeCol_TitleBarHovered, node_theme.title_bar_hover_color, category=dpg.mvThemeCat_Nodes)
            if node_theme.title_bar_selected_color is not None:
                dpg.add_theme_color(dpg.mvNodeCol_TitleBarSelected, node_theme.title_bar_selected_color, category=dpg.mvThemeCat_Nodes)
            if node_theme.outline_color is not None:
                dpg.add_theme_color(dpg.mvNodeCol_NodeOutline, node_theme.outline_color, category=dpg.mvThemeCat_Nodes)
            if node_theme.background_color is not None:
                dpg.add_theme_color(dpg.mvNodeCol_NodeBackground, node_theme.background_color, category=dpg.mvThemeCat_Nodes)
            if node_theme.background_hover_color is not None:
                dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundHovered, node_theme.background_hover_color, category=dpg.mvThemeCat_Nodes)
            if node_theme.background_selected_color is not None:
                dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundSelected, node_theme.background_selected_color, category=dpg.mvThemeCat_Nodes)
            if node_theme.corner_rounding is not None:
                dpg.add_theme_style(dpg.mvNodeStyleVar_NodeCornerRounding, node_theme.corner_rounding, category=dpg.mvThemeCat_Nodes)
            if node_theme.padding is not None:
                dpg.add_theme_style(dpg.mvNodeStyleVar_NodePadding, *node_theme.padding, category=dpg.mvThemeCat_Nodes)
            if node_theme.border_thickness is not None:
                dpg.add_theme_style(dpg.mvNodeStyleVar_NodeBorderThickness, node_theme.border_thickness, category=dpg.mvThemeCat_Nodes)
    return node_theme_ui
