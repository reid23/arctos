//! Scoped layout measurements; OBS and standalone scorers keep their own geometry.
use dioxus::prelude::*;

#[component]
pub fn ViewportMetrics() -> Element {
    #[cfg(target_arch = "wasm32")]
    {
        let observer = use_hook(|| std::rc::Rc::new(std::cell::RefCell::new(None)));
        let mount = observer.clone();
        use_effect(move || {
            if mount.borrow().is_none() {
                *mount.borrow_mut() = browser::Measurements::install();
            }
        });
        use_drop(move || {
            observer.borrow_mut().take();
        });
    }
    rsx! {}
}

/// Do not follow the visual viewport while the user is pinch-zooming: that would
/// continually move a dialog away from the region they are trying to inspect.
#[cfg(any(target_arch = "wasm32", test))]
fn visible_bounds(layout_height: f64, height: f64, top: f64, scale: f64) -> Option<(f64, f64)> {
    if ![layout_height, height, top, scale]
        .iter()
        .all(|v| v.is_finite())
        || layout_height <= 0.0
        || height <= 0.0
        || (scale - 1.0).abs() > 0.01
    {
        return None;
    }
    let height = height.min(layout_height);
    Some((height, top.clamp(0.0, layout_height - height)))
}

#[cfg(target_arch = "wasm32")]
mod browser {
    use super::visible_bounds;
    use wasm_bindgen::{closure::Closure, JsCast};
    use web_sys::{HtmlElement, ResizeObserver, VisualViewport};

    pub(super) struct Measurements {
        root: HtmlElement,
        viewport: Option<VisualViewport>,
        update: Closure<dyn FnMut(web_sys::Event)>,
        header_observer: Option<ResizeObserver>,
        _header_callback: Closure<dyn FnMut()>,
    }

    impl Measurements {
        pub(super) fn install() -> Option<Self> {
            let window = web_sys::window()?;
            let document = window.document()?;
            let root = document
                .get_element_by_id("app-layout")?
                .dyn_into::<HtmlElement>()
                .ok()?;
            let viewport = window.visual_viewport();
            let measured_root = root.clone();
            let update = Closure::wrap(Box::new(move |_: web_sys::Event| {
                update_visible_space(&measured_root);
            }) as Box<dyn FnMut(web_sys::Event)>);
            let _ =
                window.add_event_listener_with_callback("resize", update.as_ref().unchecked_ref());
            if let Some(viewport) = &viewport {
                for event in ["resize", "scroll"] {
                    let _ = viewport
                        .add_event_listener_with_callback(event, update.as_ref().unchecked_ref());
                }
            }
            update_visible_space(&root);

            let measured_root = root.clone();
            let header_callback = Closure::wrap(Box::new(move || {
                if let Ok(Some(header)) = measured_root.query_selector(".app-header") {
                    let height = header.get_bounding_client_rect().height();
                    if height.is_finite() && height > 0.0 {
                        let _ = measured_root
                            .style()
                            .set_property("--header-height", &format!("{height}px"));
                    }
                }
            }) as Box<dyn FnMut()>);
            // CSS retains the base-height + safe-area fallback if unavailable.
            let header_observer =
                ResizeObserver::new(header_callback.as_ref().unchecked_ref()).ok();
            if let (Some(observer), Ok(Some(header))) =
                (&header_observer, root.query_selector(".app-header"))
            {
                observer.observe(&header);
            }
            Some(Self {
                root,
                viewport,
                update,
                header_observer,
                _header_callback: header_callback,
            })
        }
    }

    fn update_visible_space(root: &HtmlElement) {
        let bounds = web_sys::window().and_then(|window| {
            let viewport = window.visual_viewport()?;
            visible_bounds(
                window.inner_height().ok()?.as_f64()?,
                viewport.height(),
                viewport.offset_top(),
                viewport.scale(),
            )
        });
        if let Some((height, top)) = bounds {
            let _ = root
                .style()
                .set_property("--app-viewport-height", &format!("{height}px"));
            let _ = root
                .style()
                .set_property("--app-viewport-top", &format!("{top}px"));
            let _ = root.set_attribute(
                "data-short-viewport",
                if height <= 600.0 { "true" } else { "false" },
            );
        } else {
            let _ = root.style().remove_property("--app-viewport-height");
            let _ = root.style().remove_property("--app-viewport-top");
            let _ = root.remove_attribute("data-short-viewport");
        }
    }

    impl Drop for Measurements {
        fn drop(&mut self) {
            if let Some(window) = web_sys::window() {
                let _ = window.remove_event_listener_with_callback(
                    "resize",
                    self.update.as_ref().unchecked_ref(),
                );
            }
            if let Some(viewport) = &self.viewport {
                for event in ["resize", "scroll"] {
                    let _ = viewport.remove_event_listener_with_callback(
                        event,
                        self.update.as_ref().unchecked_ref(),
                    );
                }
            }
            if let Some(observer) = &self.header_observer {
                observer.disconnect();
            }
            for property in [
                "--header-height",
                "--app-viewport-height",
                "--app-viewport-top",
            ] {
                let _ = self.root.style().remove_property(property);
            }
            let _ = self.root.remove_attribute("data-short-viewport");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::visible_bounds;

    #[test]
    fn keyboard_reduction_and_pan_stay_inside_layout() {
        assert_eq!(visible_bounds(844.0, 844.0, 0.0, 1.0), Some((844.0, 0.0)));
        assert_eq!(
            visible_bounds(844.0, 390.0, 220.0, 1.0),
            Some((390.0, 220.0))
        );
        assert_eq!(
            visible_bounds(844.0, 390.0, 500.0, 1.0),
            Some((390.0, 454.0))
        );
        assert_eq!(visible_bounds(390.0, 844.0, -20.0, 1.0), Some((390.0, 0.0)));
    }

    #[test]
    fn zoom_and_unavailable_geometry_use_css_fallback() {
        assert_eq!(visible_bounds(844.0, 422.0, 100.0, 2.0), None);
        for invalid in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert_eq!(visible_bounds(844.0, invalid, 0.0, 1.0), None);
        }
        assert_eq!(visible_bounds(844.0, 400.0, f64::NAN, 1.0), None);
        assert_eq!(visible_bounds(844.0, 400.0, 0.0, f64::NAN), None);
    }
}
