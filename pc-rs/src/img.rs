//! The few image operations the music screen needs, on plain byte buffers.

/// One channel (an alpha mask).
#[derive(Clone)]
pub struct Gray {
    pub w: usize,
    pub h: usize,
    pub px: Vec<u8>,
}

/// 8-bit RGB.
#[derive(Clone)]
pub struct Rgb {
    pub w: usize,
    pub h: usize,
    pub px: Vec<u8>,
}

#[derive(Clone, Copy)]
pub enum Filter {
    Hamming,
    Lanczos,
}

impl Filter {
    fn support(self) -> f64 {
        match self {
            Filter::Hamming => 1.0,
            Filter::Lanczos => 3.0,
        }
    }
    fn weight(self, x: f64) -> f64 {
        use std::f64::consts::PI;
        let x = x.abs();
        let sinc = |x: f64| if x == 0.0 { 1.0 } else { (x * PI).sin() / (x * PI) };
        match self {
            Filter::Hamming if x < 1.0 => sinc(x) * (0.54 + 0.46 * (x * PI).cos()),
            Filter::Lanczos if x < 3.0 => sinc(x) * sinc(x / 3.0),
            _ => 0.0,
        }
    }
}

/// For each output sample: first input index and its weights (the same scheme Pillow uses).
fn coefficients(input: usize, from: f64, to: f64, output: usize, filter: Filter) -> Vec<(usize, Vec<f32>)> {
    let scale = (to - from) / output as f64;
    let fscale = scale.max(1.0);
    let support = filter.support() * fscale;
    (0..output)
        .map(|i| {
            let center = from + (i as f64 + 0.5) * scale;
            let lo = ((center - support).floor().max(0.0) as usize).min(input.saturating_sub(1));
            let hi = ((center + support).ceil().max(0.0) as usize).clamp(lo + 1, input.max(lo + 1));
            let mut w: Vec<f64> = (lo..hi).map(|j| filter.weight((j as f64 + 0.5 - center) / fscale)).collect();
            let sum: f64 = w.iter().sum();
            if sum != 0.0 {
                w.iter_mut().for_each(|v| *v /= sum);
            }
            (lo, w.into_iter().map(|v| v as f32).collect())
        })
        .collect()
}

/// Resample the box (x0, y0, x1, y1) of an interleaved image to out_w x out_h.
fn resample(src: &[u8], w: usize, h: usize, channels: usize, boxed: [f64; 4], out_w: usize, out_h: usize, filter: Filter) -> Vec<u8> {
    if w == 0 || h == 0 || out_w == 0 || out_h == 0 {
        return vec![0; out_w * out_h * channels];
    }
    let cx = coefficients(w, boxed[0], boxed[2], out_w, filter);
    let cy = coefficients(h, boxed[1], boxed[3], out_h, filter);
    let first = cy.first().map_or(0, |c| c.0);
    let last = cy.last().map_or(h, |c| c.0 + c.1.len());
    let mut mid = vec![0f32; out_w * (last - first) * channels];
    for y in first..last {
        let row = &src[y * w * channels..(y + 1) * w * channels];
        let out = &mut mid[(y - first) * out_w * channels..];
        for (x, (lo, weights)) in cx.iter().enumerate() {
            for c in 0..channels {
                out[x * channels + c] = weights.iter().enumerate().map(|(k, k_w)| row[(lo + k) * channels + c] as f32 * k_w).sum();
            }
        }
    }
    let mut dst = vec![0u8; out_w * out_h * channels];
    for (y, (lo, weights)) in cy.iter().enumerate() {
        for x in 0..out_w * channels {
            let v: f32 = weights.iter().enumerate().map(|(k, k_w)| mid[(lo + k - first) * out_w * channels + x] * k_w).sum();
            dst[y * out_w * channels + x] = (v + 0.5).clamp(0.0, 255.0) as u8;
        }
    }
    dst
}

/// Separable Gaussian blur, sigma = radius, edges repeated.
fn blur(px: &mut [u8], w: usize, h: usize, channels: usize, sigma: f64) {
    if sigma <= 0.0 || w == 0 || h == 0 {
        return;
    }
    let r = (sigma * 2.6).ceil() as isize;
    let mut k: Vec<f32> = (-r..=r).map(|i| (-((i * i) as f64) / (2.0 * sigma * sigma)).exp() as f32).collect();
    let sum: f32 = k.iter().sum();
    k.iter_mut().for_each(|v| *v /= sum);
    let mut mid = vec![0f32; px.len()];
    for y in 0..h {
        for x in 0..w {
            for c in 0..channels {
                mid[(y * w + x) * channels + c] = k
                    .iter()
                    .enumerate()
                    .map(|(i, kv)| {
                        let sx = (x as isize + i as isize - r).clamp(0, w as isize - 1) as usize;
                        px[(y * w + sx) * channels + c] as f32 * kv
                    })
                    .sum();
            }
        }
    }
    for y in 0..h {
        let rows: Vec<usize> = (-r..=r).map(|i| (y as isize + i).clamp(0, h as isize - 1) as usize * w * channels).collect();
        for x in 0..w * channels {
            let v: f32 = k.iter().zip(&rows).map(|(kv, row)| mid[row + x] * kv).sum();
            px[y * w * channels + x] = (v + 0.5).min(255.0) as u8;
        }
    }
}

fn mul(a: u8, b: u8) -> u8 {
    ((a as u32 * b as u32 + 127) / 255) as u8
}

pub fn mix(under: u8, over: u8, a: u8) -> u8 {
    ((under as u32 * (255 - a as u32) + over as u32 * a as u32 + 127) / 255) as u8
}

impl Gray {
    pub fn new(w: usize, h: usize) -> Self {
        Self { w, h, px: vec![0; w * h] }
    }

    /// The rows y0..y0+h, zero where they fall outside the image.
    pub fn rows(&self, y0: isize, h: usize) -> Gray {
        let mut out = Gray::new(self.w, h);
        for y in 0..h {
            let sy = y0 + y as isize;
            if sy >= 0 && (sy as usize) < self.h {
                let s = sy as usize * self.w;
                out.px[y * self.w..(y + 1) * self.w].copy_from_slice(&self.px[s..s + self.w]);
            }
        }
        out
    }

    pub fn columns(&self, x0: usize, x1: usize) -> Gray {
        let (x0, x1) = (x0.min(self.w), x1.min(self.w));
        let mut out = Gray::new(x1.saturating_sub(x0), self.h);
        for y in 0..self.h {
            out.px[y * out.w..(y + 1) * out.w].copy_from_slice(&self.px[y * self.w + x0..y * self.w + x1]);
        }
        out
    }

    /// Keep the brighter of what is there and `src` placed at (x, y); parts outside are dropped.
    pub fn lighten(&mut self, src: &Gray, x: isize, y: isize) {
        for sy in 0..src.h {
            let ty = y + sy as isize;
            if ty < 0 || ty as usize >= self.h {
                continue;
            }
            for sx in 0..src.w {
                let tx = x + sx as isize;
                if tx >= 0 && (tx as usize) < self.w {
                    let d = &mut self.px[ty as usize * self.w + tx as usize];
                    *d = (*d).max(src.px[sy * src.w + sx]);
                }
            }
        }
    }

    pub fn scale_values(&mut self, f: f64) {
        self.px.iter_mut().for_each(|v| *v = (*v as f64 * f).round().clamp(0.0, 255.0) as u8);
    }

    /// Multiply every row by the same per-column values.
    pub fn multiply_columns(&mut self, by: &[u8]) {
        for row in self.px.chunks_mut(self.w) {
            row.iter_mut().zip(by).for_each(|(v, b)| *v = mul(*v, *b));
        }
    }

    /// Multiply every column by the same per-row values.
    pub fn multiply_rows(&mut self, by: &[u8]) {
        for (row, b) in self.px.chunks_mut(self.w).zip(by) {
            row.iter_mut().for_each(|v| *v = mul(*v, *b));
        }
    }

    pub fn resample(&self, boxed: [f64; 4], w: usize, h: usize, filter: Filter) -> Gray {
        Gray { w, h, px: resample(&self.px, self.w, self.h, 1, boxed, w, h, filter) }
    }

    /// The same picture with an empty margin of m pixels.
    pub fn expand(&self, m: usize) -> Gray {
        let mut out = Gray::new(self.w + 2 * m, self.h + 2 * m);
        out.lighten(self, m as isize, m as isize);
        out
    }

    pub fn blur(&mut self, sigma: f64) {
        blur(&mut self.px, self.w, self.h, 1, sigma);
    }

    pub fn blend_pixel(&mut self, x: i32, y: i32, value: u8, coverage: f32) {
        if x >= 0 && y >= 0 && (x as usize) < self.w && (y as usize) < self.h {
            let d = &mut self.px[y as usize * self.w + x as usize];
            *d = mix(*d, value, (coverage.clamp(0.0, 1.0) * 255.0).round() as u8);
        }
    }
}

impl Rgb {
    pub fn new(w: usize, h: usize, color: [u8; 3]) -> Self {
        Self { w, h, px: color.iter().copied().cycle().take(w * h * 3).collect() }
    }

    pub fn get(&self, x: usize, y: usize) -> [u8; 3] {
        let i = (y * self.w + x) * 3;
        [self.px[i], self.px[i + 1], self.px[i + 2]]
    }

    pub fn blend_pixel(&mut self, x: i32, y: i32, color: [u8; 3], coverage: f32) {
        if x >= 0 && y >= 0 && (x as usize) < self.w && (y as usize) < self.h {
            let a = (coverage.clamp(0.0, 1.0) * 255.0).round() as u8;
            let i = (y as usize * self.w + x as usize) * 3;
            for c in 0..3 {
                self.px[i + c] = mix(self.px[i + c], color[c], a);
            }
        }
    }

    /// Anti-aliased fill of a shape given as a signed distance (negative inside) over a pixel box.
    pub fn fill_shape(&mut self, boxed: [f64; 4], color: [u8; 3], alpha: f32, distance: impl Fn(f64, f64) -> f64) {
        let [x0, y0, x1, y1] = boxed;
        for y in y0.floor() as i32..y1.ceil() as i32 {
            for x in x0.floor() as i32..x1.ceil() as i32 {
                let c = (0.5 - distance(x as f64 + 0.5, y as f64 + 0.5)).clamp(0.0, 1.0) as f32;
                if c > 0.0 {
                    self.blend_pixel(x, y, color, c * alpha);
                }
            }
        }
    }

    /// Paint `color` through a mask placed at (0, y).
    pub fn paint(&mut self, mask: &Gray, y: usize, color: [u8; 3]) {
        for my in 0..mask.h.min(self.h.saturating_sub(y)) {
            for x in 0..mask.w.min(self.w) {
                let a = mask.px[my * mask.w + x];
                if a != 0 {
                    let i = ((y + my) * self.w + x) * 3;
                    for c in 0..3 {
                        self.px[i + c] = mix(self.px[i + c], color[c], a);
                    }
                }
            }
        }
    }

    /// Copy `src` to (x, y) with rounded corners of radius r.
    pub fn paste_rounded(&mut self, src: &Rgb, x: usize, y: usize, r: f64) {
        let (w, h) = (src.w as f64, src.h as f64);
        for sy in 0..src.h {
            for sx in 0..src.w {
                let d = rounded_rect(sx as f64 + 0.5, sy as f64 + 0.5, [0.0, 0.0, w, h], r);
                let c = (0.5 - d).clamp(0.0, 1.0) as f32;
                self.blend_pixel((x + sx) as i32, (y + sy) as i32, src.get(sx, sy), c);
            }
        }
    }

    pub fn crop(&self, x: usize, y: usize, w: usize, h: usize) -> Rgb {
        let mut px = Vec::with_capacity(w * h * 3);
        for row in y..y + h {
            let i = (row * self.w + x) * 3;
            px.extend_from_slice(&self.px[i..i + w * 3]);
        }
        Rgb { w, h, px }
    }

    pub fn resample(&self, boxed: [f64; 4], w: usize, h: usize, filter: Filter) -> Rgb {
        Rgb { w, h, px: resample(&self.px, self.w, self.h, 3, boxed, w, h, filter) }
    }

    pub fn blur(&mut self, sigma: f64) {
        blur(&mut self.px, self.w, self.h, 3, sigma);
    }

    pub fn darken(&mut self, keep: f64) {
        self.px.iter_mut().for_each(|v| *v = (*v as f64 * keep).round() as u8);
    }
}

/// Signed distance to a rounded rectangle [x0, y0, x1, y1].
pub fn rounded_rect(x: f64, y: f64, [x0, y0, x1, y1]: [f64; 4], r: f64) -> f64 {
    let qx = ((x0 + r) - x).max(x - (x1 - r));
    let qy = ((y0 + r) - y).max(y - (y1 - r));
    (qx.max(0.0).powi(2) + qy.max(0.0).powi(2)).sqrt() + qx.max(qy).min(0.0) - r
}
