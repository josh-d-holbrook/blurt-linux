"""App-local styling and a small, non-focusing timer pill."""
import math
import time
import cairo
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo


def apply_theme():
    settings = Gtk.Settings.get_default()
    settings.set_property('gtk-theme-name', 'Adwaita')
    settings.set_property('gtk-application-prefer-dark-theme', True)
    css = Gtk.CssProvider()
    css.load_from_data(b'''
        #blurt-main { background: #202522; color: #e5ebe6; }
        #blurt-main label { color: #e5ebe6; }
        #blurt-main label.dim-label { color: #a2afa5; }
        #blurt-header { background: #202522; border: none; box-shadow: none; }
        #blurt-main notebook { background: #252c27; border: 1px solid #3b463e; border-radius: 12px; }
        #blurt-main notebook header { background: #292f2b; border: none; }
        #blurt-main notebook tab { padding: 9px 18px; }
        #blurt-main entry, #blurt-main combobox button {
            background: #303832; color: #e5ebe6; border: 1px solid #47554b;
            border-radius: 8px; box-shadow: none; min-height: 27px;
        }
        #blurt-main notebook tab:checked { border-color: #8ddbaa; }
        #blurt-main button { border-radius: 8px; box-shadow: none; min-height: 27px; }
        #blurt-main button.suggested-action { background: #087a42; color: white; border-color: #087a42; }
        #blurt-main button.suggested-action label { color: white; }
        #blurt-main textview text, #blurt-main treeview { background: #252c27; color: #e5ebe6; }
        #blurt-main treeview:selected { background: #294c38; color: #dcf5e5; }
        window#blurt-overlay { background: transparent; }
    ''')
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


# Geometry, palette, gradient stops, and bar math follow upstream Blurt.
PILL_WIDTH, PILL_HEIGHT, SHADOW_MARGIN = 138, 28, 28
PANEL_WIDTH, PANEL_HEIGHT = PILL_WIDTH + 56, PILL_HEIGHT + 56
GREEN = (103 / 255, 173 / 255, 130 / 255)
ORANGE = (230 / 255, 127 / 255, 54 / 255)


def capsule(cr, x, y, width, height):
    radius = min(width, height) / 2
    cr.new_sub_path()
    cr.arc(x+width-radius, y+radius, radius, -math.pi/2, 0)
    cr.arc(x+width-radius, y+height-radius, radius, 0, math.pi/2)
    cr.arc(x+radius, y+height-radius, radius, math.pi/2, math.pi)
    cr.arc(x+radius, y+radius, radius, math.pi, 3*math.pi/2)
    cr.close_path()


def meter_level(db):
    return min(1.0, max(0.0, (db + 50) / 50)) if math.isfinite(db) else 0.0


def countdown_value(audio_seconds):
    remaining = max(0.0, 119 - audio_seconds)
    return math.ceil(remaining) if remaining <= 20 else None


class PillRenderer:
    """Cache the capsule/shadow/orb; draw only the active ring and content per tick."""
    def __init__(self, scale=1):
        self.scale = scale
        self.background = cairo.ImageSurface(cairo.FORMAT_ARGB32, PANEL_WIDTH * scale, PANEL_HEIGHT * scale)
        self.background.set_device_scale(scale, scale)
        cr = cairo.Context(self.background)
        # A one-time nested Gaussian falloff, rather than a blur on every frame.
        previous = 0.0
        for half in range(52, -1, -1):
            spread = half / 2
            opacity = .25 * math.exp(-.5 * (spread / 7) ** 2)
            cr.set_source_rgba(0, 0, 0, (opacity - previous) / (1 - previous))
            capsule(cr, 28-spread, 31-spread, 138+2*spread, 28+2*spread)
            cr.fill()
            previous = opacity
        capsule(cr, 28, 28, 138, 28)
        cr.set_source_rgb(29/255, 27/255, 22/255)
        cr.fill()
        capsule(cr, 28.5, 28.5, 137, 27)
        cr.set_source_rgba(1, 1, 1, .12)
        cr.set_line_width(1)
        cr.stroke()
        self.orb_x, self.orb_y, self.radius = 48.4, 42, 8.4
        gradient = cairo.LinearGradient(0, 50.4, 0, 33.6)
        for stop, rgb in [(0,(215,211,244)),(.0673,(176,167,233)),(.1442,(103,173,130)),
                          (.3029,(1,118,47)),(.5962,(57,35,199)),(.75,(136,123,221)),
                          (.8942,(215,211,244)),(1,(255,255,255))]:
            gradient.add_color_stop_rgb(stop, *(c/255 for c in rgb))
        cr.arc(self.orb_x, self.orb_y, self.radius, 0, 2*math.pi)
        cr.set_source(gradient)
        cr.fill()
        self.weights = [.45 + .55 * math.sin(math.pi * i / 13) for i in range(14)]
        self.layouts = {}

    def text(self, cr, text, timer=False):
        key = text, timer
        if key not in self.layouts:
            layout = PangoCairo.create_layout(cr)
            font = Pango.FontDescription('Monospace' if timer else 'Sans')
            font.set_absolute_size((13 if timer else 9) * Pango.SCALE)
            font.set_weight(Pango.Weight.MEDIUM if timer else Pango.Weight.SEMIBOLD)
            layout.set_font_description(font)
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_letter_spacing_new(int((.5 if timer else .9) * Pango.SCALE)))
            layout.set_attributes(attrs)
            layout.set_text(text, -1)
            self.layouts[key] = layout
        layout = self.layouts[key]
        width, height = layout.get_pixel_size()
        # Fit platform font differences inside the same 89.2-pixel content field.
        ratio = min(1, 89.2 / max(1, width))
        cr.save()
        cr.translate(64.8 + ((89.2-width*ratio)/2 if timer else 0), 42-height/2)
        cr.scale(ratio, 1)
        PangoCairo.show_layout(cr, layout)
        cr.restore()

    def draw(self, cr, mode, seconds, level, now, animated=True):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source_surface(self.background, 0, 0)
        cr.paint()
        cr.save()
        cr.translate(self.orb_x, self.orb_y)
        cr.rotate((now % 1.6) / 1.6 * 2 * math.pi if animated else 0)
        ring = cairo.LinearGradient(-8.4, -8.4, 8.4, 8.4)
        ring.add_color_stop_rgb(0, 1/255, 118/255, 47/255)
        ring.add_color_stop_rgb(1, 1, 1, 1)
        cr.set_source(ring)
        cr.arc(0, 0, 7.9, 0, 2*math.pi)
        cr.set_line_width(1)
        cr.stroke()
        cr.restore()
        value = countdown_value(seconds)
        cr.set_source_rgb(*GREEN)
        if mode != 'recording':
            self.text(cr, mode.upper())
        elif value is not None:
            cr.set_source_rgb(*ORANGE)
            self.text(cr, f'{value//60}:{value%60:02d}', timer=True)
        else:
            for i, weight in enumerate(self.weights):
                voice = level ** 1.3 * weight
                idle = max(0, 1-voice/.25) if animated else 0
                breath = .12*weight*(math.sin(now*math.pi-i*.45)+1)/2*idle
                height = 22*min(1, max(.12, voice+breath))
                # 14 bars, 3 pixels wide with 3-pixel gaps, centered in the field.
                cr.save()
                cr.translate(67.9+i*6, 42)
                cr.rotate(math.pi/2)
                capsule(cr, -height/2, -1.5, height, 3)
                cr.fill()
                cr.restore()


class CompactOverlay(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_name('blurt-overlay')
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_keep_above(True)
        self.set_decorated(False)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(PANEL_WIDTH, PANEL_HEIGHT)
        self.add(self.area)
        self.renderer = None
        self.mode, self.seconds, self.level = 'recording', 0.0, 0.0
        self.animation_id = None
        self.fade_start, self.fade_from, self.fade_target = 0.0, 1.0, 1.0
        self.last_description = None
        self.area.connect('draw', self.draw)
        self.connect('realize', self.realized)
        self.connect('destroy', self.stop_animation)

    def realized(self, *_):
        self.input_shape_combine_region(cairo.Region(cairo.RectangleInt(28, 28, 138, 28)))

    def animations_enabled(self):
        return bool(Gtk.Settings.get_default().get_property('gtk-enable-animations'))

    def describe(self, text):
        if text != self.last_description:
            self.set_tooltip_text(text)
            self.get_accessible().set_name(text)
            self.last_description = text

    def recording(self, audio_seconds, power_db=-120):
        self.mode, self.seconds, self.level = 'recording', audio_seconds, meter_level(power_db)
        value = countdown_value(audio_seconds)
        self.describe((f'{value} seconds left. ' if value is not None else 'Recording. ') +
                      'Esc pauses and keeps your recording in History.')
        if self.animation_id is None:
            self.area.queue_draw()

    def processing(self, text='Transcribing'):
        self.mode = text.lower()
        self.describe(f'{text}. Audio is saved; the result stays in History.')
        self.area.queue_draw()

    def draw(self, widget, cr):
        scale = self.get_scale_factor()
        if self.renderer is None or self.renderer.scale != scale:
            self.renderer = PillRenderer(scale)
        self.renderer.draw(cr, self.mode, self.seconds, self.level, time.monotonic(), self.animations_enabled())
        return False

    def stop_animation(self, *_):
        if self.animation_id is not None:
            GLib.source_remove(self.animation_id)
            self.animation_id = None

    def tick(self):
        duration = .08 if self.fade_target else .2
        fraction = min(1, (time.monotonic()-self.fade_start)/duration)
        self.set_opacity(self.fade_from+(self.fade_target-self.fade_from)*fraction)
        if fraction >= 1 and not self.fade_target:
            self.animation_id = None
            Gtk.Widget.hide(self)
            return False
        if not self.animations_enabled():
            self.set_opacity(self.fade_target)
            if not self.fade_target:
                Gtk.Widget.hide(self)
            self.animation_id = None
            return False
        self.area.queue_draw()
        return True

    def present_pill(self):
        was_visible = self.get_visible()
        self.show_all()
        if not was_visible:
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            area = monitor.get_workarea()
            self.move(area.x+(area.width-PANEL_WIDTH)//2,
                      area.y+area.height-80-PILL_HEIGHT-SHADOW_MARGIN)
            self.set_opacity(0 if self.animations_enabled() else 1)
        self.fade_start, self.fade_from, self.fade_target = time.monotonic(), self.get_opacity(), 1.0
        if self.animation_id is None and self.animations_enabled():
            self.animation_id = GLib.timeout_add(50, self.tick)
        self.area.queue_draw()

    def hide(self):
        if self.get_visible() and self.animations_enabled():
            self.fade_start, self.fade_from, self.fade_target = time.monotonic(), self.get_opacity(), 0.0
            if self.animation_id is None:
                self.animation_id = GLib.timeout_add(50, self.tick)
        else:
            self.stop_animation()
            Gtk.Widget.hide(self)
