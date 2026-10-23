#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CropRect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// Source coordinates for a crop that always fills the chosen aspect ratio.
pub fn crop_rect(width: f64, height: f64, aspect: f64, zoom: f64, x: f64, y: f64) -> CropRect {
    let crop_width = width.min(height * aspect) / zoom.clamp(1.0, 3.0);
    let crop_height = crop_width / aspect;
    CropRect {
        x: (width - crop_width).max(0.0) * x.clamp(0.0, 100.0) / 100.0,
        y: (height - crop_height).max(0.0) * y.clamp(0.0, 100.0) / 100.0,
        width: crop_width,
        height: crop_height,
    }
}

/// Center the visible point selected by a tap, without changing crop size or zoom.
/// Coordinates are fractions of the viewport; panning stops at the source edges.
pub fn focus_crop(width: f64, height: f64, crop: CropRect, x: f64, y: f64) -> (f64, f64) {
    let position = |size: f64, origin: f64, extent: f64, point: f64| {
        let available = size - extent;
        if available <= 0.0 {
            50.0
        } else {
            ((origin + point.clamp(0.0, 1.0) * extent - extent / 2.0) / available * 100.0)
                .clamp(0.0, 100.0)
        }
    };
    (
        position(width, crop.x, crop.width, x),
        position(height, crop.y, crop.height, y),
    )
}

/// Check dimensions from file headers before a browser allocates decoded pixels.
pub fn validate_source(bytes: &[u8]) -> Result<&'static str, String> {
    if bytes.is_empty() || bytes.len() > 8 * 1024 * 1024 {
        return Err("Choose a non-empty image no larger than 8 MB.".into());
    }
    let kind =
        imagesize::image_type(bytes).map_err(|_| "Choose a valid JPEG, PNG, or WebP image.")?;
    let mime = match kind {
        imagesize::ImageType::Jpeg => "image/jpeg",
        imagesize::ImageType::Png | imagesize::ImageType::Webp => "image/png",
        _ => return Err("Choose a JPEG, PNG, or WebP image.".into()),
    };
    let size =
        imagesize::blob_size(bytes).map_err(|_| "Choose a valid JPEG, PNG, or WebP image.")?;
    if size.width == 0
        || size.height == 0
        || size.width > 8192
        || size.height > 8192
        || size.width.saturating_mul(size.height) > 24_000_000
    {
        return Err("Choose an image up to 8192 pixels per side and 24 megapixels.".into());
    }
    Ok(mime)
}

#[cfg(target_arch = "wasm32")]
pub async fn load_image(url: &str) -> Result<web_sys::HtmlImageElement, String> {
    let image = web_sys::HtmlImageElement::new().map_err(|_| "Could not open this image.")?;
    image.set_src(url);
    wasm_bindgen_futures::JsFuture::from(image.decode())
        .await
        .map_err(|_| "Choose a valid JPEG, PNG, or WebP image.")?;
    let width = image.natural_width();
    let height = image.natural_height();
    if width == 0
        || height == 0
        || width > 8192
        || height > 8192
        || u64::from(width) * u64::from(height) > 24_000_000
    {
        return Err("Choose an image up to 8192 pixels per side and 24 megapixels.".into());
    }
    Ok(image)
}

#[cfg(target_arch = "wasm32")]
pub async fn encode_crop(
    url: &str,
    crop: CropRect,
    avatar: bool,
    mime: &str,
) -> Result<bytes::Bytes, String> {
    use wasm_bindgen::{JsCast, JsValue};
    use wasm_bindgen_futures::JsFuture;
    let image = load_image(url).await?;
    let document = web_sys::window()
        .and_then(|window| window.document())
        .ok_or("Image editing is unavailable.")?;
    let canvas = document
        .create_element("canvas")
        .map_err(|_| "Could not prepare the image.")?
        .dyn_into::<web_sys::HtmlCanvasElement>()
        .map_err(|_| "Could not prepare the image.")?;
    let aspect = if avatar { 1 } else { 3 };
    let maximum = if avatar { 512 } else { 1500 };
    let width = maximum.min((crop.width.floor() as u32).max(aspect)) / aspect * aspect;
    let height = width / aspect;
    canvas.set_width(width);
    canvas.set_height(height);
    let context = canvas
        .get_context("2d")
        .map_err(|_| "Could not prepare the image.")?
        .ok_or("Could not prepare the image.")?
        .dyn_into::<web_sys::CanvasRenderingContext2d>()
        .map_err(|_| "Could not prepare the image.")?;
    context
        .draw_image_with_html_image_element_and_sw_and_sh_and_dx_and_dy_and_dw_and_dh(
            &image,
            crop.x,
            crop.y,
            crop.width,
            crop.height,
            0.0,
            0.0,
            f64::from(width),
            f64::from(height),
        )
        .map_err(|_| "Could not crop this image. Try another image.")?;
    let encoded = js_sys::Promise::new(&mut |resolve, reject| {
        let resolve = resolve.clone();
        let callback = wasm_bindgen::closure::Closure::once_into_js(move |blob: JsValue| {
            let _ = resolve.call1(&JsValue::NULL, &blob);
        });
        if let Err(error) = canvas.to_blob_with_type_and_encoder_options(
            callback.unchecked_ref(),
            mime,
            &JsValue::from_f64(0.88),
        ) {
            let _ = reject.call1(&JsValue::NULL, &error);
        }
    });
    let blob = JsFuture::from(encoded)
        .await
        .map_err(|_| "Could not save the cropped image.")?
        .dyn_into::<web_sys::Blob>()
        .map_err(|_| "Could not save the cropped image.")?;
    let buffer = JsFuture::from(blob.array_buffer())
        .await
        .map_err(|_| "Could not read the cropped image.")?;
    Ok(bytes::Bytes::from(
        js_sys::Uint8Array::new(&buffer).to_vec(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tap_centers_visible_point_and_preserves_crop_size() {
        let before = crop_rect(1200.0, 800.0, 3.0, 2.0, 50.0, 50.0);
        let (x, y) = focus_crop(1200.0, 800.0, before, 0.75, 0.25);
        let after = crop_rect(1200.0, 800.0, 3.0, 2.0, x, y);
        assert_eq!((after.width, after.height), (before.width, before.height));
        assert!((after.x + after.width / 2.0 - before.x - before.width * 0.75).abs() < 1e-6);
        assert!((after.y + after.height / 2.0 - before.y - before.height * 0.25).abs() < 1e-6);
        assert_eq!(focus_crop(1200.0, 800.0, before, 0.5, 0.5), (50.0, 50.0));
    }

    #[test]
    fn repeated_taps_reach_source_edges_without_dragging() {
        for aspect in [1.0, 3.0] {
            for edge in [0.0, 1.0] {
                let (mut x, mut y) = (50.0, 50.0);
                for _ in 0..30 {
                    let crop = crop_rect(1200.0, 800.0, aspect, 3.0, x, y);
                    (x, y) = focus_crop(1200.0, 800.0, crop, edge, edge);
                }
                assert_eq!((x, y), (edge * 100.0, edge * 100.0));
            }
        }
        let crop = crop_rect(600.0, 200.0, 3.0, 1.0, 50.0, 50.0);
        assert_eq!(focus_crop(600.0, 200.0, crop, -1.0, 2.0), (50.0, 50.0));
    }

    #[test]
    fn oversized_source_is_rejected_before_decoding() {
        let mut png = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR".to_vec();
        png.extend_from_slice(&10000u32.to_be_bytes());
        png.extend_from_slice(&10000u32.to_be_bytes());
        png.extend_from_slice(&[8, 6, 0, 0, 0]);
        assert!(validate_source(&png).unwrap_err().contains("24 megapixels"));
        assert!(validate_source(b"<svg></svg>").is_err());
        assert!(validate_source(&vec![0; 8 * 1024 * 1024 + 1]).is_err());
    }

    #[test]
    fn portrait_cover_keeps_exact_aspect_and_reaches_both_edges() {
        let top = crop_rect(900.0, 1800.0, 3.0, 1.0, 50.0, 0.0);
        let bottom = crop_rect(900.0, 1800.0, 3.0, 1.0, 50.0, 100.0);
        assert_eq!(
            (top.width, top.height, top.x, top.y),
            (900.0, 300.0, 0.0, 0.0)
        );
        assert_eq!(bottom.y + bottom.height, 1800.0);
    }

    #[test]
    fn square_avatar_crops_landscape_without_stretching() {
        let crop = crop_rect(1600.0, 800.0, 1.0, 1.0, 50.0, 50.0);
        assert_eq!(
            crop,
            CropRect {
                x: 400.0,
                y: 0.0,
                width: 800.0,
                height: 800.0
            }
        );
    }

    #[test]
    fn zoom_and_drag_never_expose_empty_pixels() {
        for aspect in [1.0, 3.0] {
            for zoom in [0.0, 1.0, 2.0, 3.0, 10.0] {
                for position in [-100.0, 0.0, 50.0, 100.0, 200.0] {
                    let crop = crop_rect(2400.0, 1600.0, aspect, zoom, position, position);
                    assert!(crop.x >= 0.0 && crop.y >= 0.0);
                    assert!(crop.x + crop.width <= 2400.0 + 1e-6);
                    assert!(crop.y + crop.height <= 1600.0 + 1e-6);
                    assert!((crop.width / crop.height - aspect).abs() < 1e-6);
                }
            }
        }
    }
}
