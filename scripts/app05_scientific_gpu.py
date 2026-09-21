"""Native texture shader plus stencil curves/coverage for detached scientific layers.

Experimental bounded resources, ephemeral by default or explicitly context-owned.
Never wired into product. Full uploads, not a throughput benchmark. No GL wide lines.
"""
from math import ceil, floor, isfinite
from scripts.app05_gpu_errors import GpuOperationError


def physical_scissor(clip, extent):
    """Half-open pixel-centre coverage with top-left logical scene coordinates."""
    x, y, width, height = clip
    left = max(0, min(extent.pixel_width, ceil(x * extent.dpr - .5)))
    right = max(left, min(extent.pixel_width, ceil((x + width) * extent.dpr - .5)))
    top = max(0, min(extent.pixel_height, ceil(y * extent.dpr - .5)))
    bottom = max(top, min(extent.pixel_height, ceil((y + height) * extent.dpr - .5)))
    return left, extent.pixel_height - bottom, right - left, bottom - top


def texture_vertices():
    import numpy as np
    # Full-target quad: exact integer scissor alone owns pixel-centre coverage.
    # Avoid GL top-left triangle rules rejecting a valid half-pixel boundary.
    # Scientific coordinates remain float64 shader uniforms, not RF float32.
    return np.asarray([(-1, 1), (1, 1), (-1, -1), (1, -1)], dtype=np.float32)


def image_scissor(layer, extent):
    """Intersect exact image bounds too, BEFORE float32 triangle quantization."""
    x, y, width, height = layer.target_rect()
    cx, cy, cw, ch = layer.clip
    left, right = max(min(x, x + width), cx), min(max(x, x + width), cx + cw)
    top, bottom = max(min(y, y + height), cy), min(max(y, y + height), cy + ch)
    x0 = max(0, min(extent.pixel_width, ceil(left * extent.dpr - .5)))
    x1 = max(x0, min(extent.pixel_width, ceil(right * extent.dpr - .5)))
    y0 = max(0, min(extent.pixel_height, ceil(top * extent.dpr - .5)))
    y1 = max(y0, min(extent.pixel_height, ceil(bottom * extent.dpr - .5)))
    return x0, extent.pixel_height-y1, x1-x0, y1-y0


def sampled_image_scissor(layer, extent):
    """Explicit Qt raster rectangle rounding; default mathematical rule stays intact."""
    if layer.sampling == "pixel-centre":
        return image_scissor(layer, extent)
    if layer.sampling != "qt611-nearest":
        raise ValueError("unknown image sampling contract")
    x, y, w, h = layer.target_rect()
    def rounded(v):
        return floor(v + .5) if v >= 0 else ceil(v - .5)
    left, right = sorted((rounded(x*extent.dpr), rounded((x+w)*extent.dpr)))
    top, bottom = sorted((rounded(y*extent.dpr), rounded((y+h)*extent.dpr)))
    cx, cy, cw, ch = layer.clip
    left = max(0, min(extent.pixel_width, max(left, rounded(cx*extent.dpr))))
    right = max(left, min(extent.pixel_width, right, rounded((cx+cw)*extent.dpr)))
    top = max(0, min(extent.pixel_height, max(top, rounded(cy*extent.dpr))))
    bottom = max(top, min(extent.pixel_height, bottom, rounded((cy+ch)*extent.dpr)))
    return left, extent.pixel_height-bottom, max(0, right-left), max(0, bottom-top)


def qt_sampling_inverse(layer, extent):
    """Narrow Qt 6.11.1 generic 16.16 path, never an automatic quality fallback.

    Source: Qt v6.11.1 QSpanData::setupMatrix and fetchTransformed_fetcher.
    Reject paths with different raster rules rather than silently approximating.
    The detached image is already premultiplied; only sampling changes here.
    """
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QImage, QTransform
    if qVersion() != "6.11.1":
        raise ValueError("Qt raster compatibility verified only for Qt 6.11.1")
    if layer.sampling != "qt611-nearest" or layer.source_format not in (3, 17) or layer.opacity != 1.:
        raise ValueError("Qt sampling requires explicit mode, Indexed8/RGBA8888 and opacity one")
    if layer.image is None or layer.image.isNull() or layer.local_rect is None:
        raise ValueError("Qt sampling requires a detached image and rectangle")
    if layer.image.format() != QImage.Format.Format_RGBA8888_Premultiplied:
        raise ValueError("Qt sampling requires premultiplied RGBA detached pixels")
    if not all(isfinite(v) for v in (*layer.transform, *layer.local_rect, *layer.clip)):
        raise ValueError("Qt sampling requires finite geometry")
    matrix = layer.matrix() * QTransform.fromScale(extent.dpr, extent.dpr)
    if matrix.type() != QTransform.TransformationType.TxScale or matrix.m11() <= 0 or matrix.m22() == 0:
        raise ValueError("Qt sampling requires positive X axis-aligned scaling")
    x, y, w, h = layer.local_rect
    if w <= 0 or h <= 0 or layer.clip[2] < 0 or layer.clip[3] < 0:
        raise ValueError("Qt sampling requires positive image extent and nonnegative clip")
    matrix.translate(x, y)
    matrix.scale(w/layer.image.width(), h/layer.image.height())
    # Source-space displacement is part of Qt's rounding, not a tolerance.
    delta = QTransform.fromTranslate(1./65536, 1./65536)
    inverse, valid = (delta * matrix).inverted()
    sx, sy, dx, dy = inverse.m11(), inverse.m22(), inverse.dx(), inverse.dy()
    if not valid or not (1./65536 < sx*sx < 1e4 and 1./65536 < sy*sy < 1e4
                         and abs(dx) < 1e4 and abs(dy) < 1e4):
        raise ValueError("Qt sampling outside fast-matrix contract")
    # Conservative whole-target bound includes one complete 2048-pixel span.
    # This rejects overflow / Qt's floating fallback before allocating GL state.
    if max(abs(sx*.5+dx), abs(sx*(extent.pixel_width+2048.5)+dx),
           abs(sy*.5+dy), abs(sy*(extent.pixel_height+.5)+dy)) >= 32767:
        raise ValueError("Qt sampling outside signed 16.16 coordinate range")
    return sx, sy, dx, dy


def draw_scientific_gpu(device, functions, extent, bundle, *, curves=True, resources=None):
    from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture, QOpenGLFunctions_4_0_Core
    from scripts.app05_vector_gpu import draw_vectors_gpu
    for layer in bundle.layers:
        if layer.image is not None:
            if layer.sampling == "qt611-nearest":
                qt_sampling_inverse(layer, extent)
            elif layer.sampling != "pixel-centre":
                raise ValueError("unknown image sampling contract")
    if curves:
        for layer in bundle.layers:
            if layer.image is not None and any(other.image is None and other.panel == layer.panel and other.z <= layer.z
                                               for other in bundle.layers):
                raise ValueError("prototype requires images below vector layers within each panel")
    largest_image = max((layer.image.sizeInBytes() for layer in bundle.layers
                         if layer.image is not None), default=0)
    if extent.nominal_target_bytes + bundle.retained_bytes + largest_image * 2 + 64 > extent.target_budget_bytes:
        raise MemoryError("combined scientific target/payload/upload budget exceeded")
    if resources is not None:
        resources.set_budget(extent.target_budget_bytes-extent.nominal_target_bytes-bundle.retained_bytes)
    doubles = QOpenGLFunctions_4_0_Core()
    if not doubles.initializeOpenGLFunctions():
        raise GpuOperationError("scientific texel prototype requires OpenGL 4.0; select CPU fallback")
    program = QOpenGLShaderProgram()
    buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
    texture = None
    uploads = 0
    max_texture_bytes = 0
    def check(stage):
        error = functions.glGetError()
        if error:
            raise GpuOperationError(f"scientific GL error {error} at {stage}")
    try:
        check("entry")
        vertex = """#version 400
            in vec2 position; void main(){gl_Position=vec4(position,0.,1.);}"""
        fragment = """#version 400
            uniform sampler2D pixels; uniform float layerOpacity;
            uniform dvec4 pixelRect; uniform double deviceRatio; uniform double targetHeight;
            uniform int qtNearest; uniform int spanStart;
            uniform dvec4 qtInverse;
            out vec4 fragmentColor;
            void main(){
                precise dvec2 point=dvec2(gl_FragCoord.x,targetHeight-double(gl_FragCoord.y))/deviceRatio;
                precise dvec2 samplePosition=(point-pixelRect.xy)*dvec2(textureSize(pixels,0))/pixelRect.zw;
                if(qtNearest!=0){
                    // Explicit Qt 6.11 generic raster compatibility, not the
                    // default mathematical pixel-centre sampling contract.
                    int offset=int(floor(gl_FragCoord.x))-spanStart;
                    int within=offset%2048;
                    precise double base=qtInverse.x*(double(spanStart+offset-within)+0.5);
                    base=base+qtInverse.z;
                    precise double row=qtInverse.y*(targetHeight-double(gl_FragCoord.y));
                    row=row+qtInverse.w;
                    samplePosition.x=(trunc(base*65536.0)+double(within)*trunc(qtInverse.x*65536.0))/65536.0;
                    samplePosition.y=trunc(row*65536.0)/65536.0;
                }
                ivec2 index=clamp(ivec2(floor(samplePosition)),ivec2(0),textureSize(pixels,0)-ivec2(1));
                fragmentColor=texelFetch(pixels,index,0)*layerOpacity;
            }"""
        if resources is None:
            if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex):
                raise GpuOperationError(program.log())
            if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment):
                raise GpuOperationError(program.log())
            program.bindAttributeLocation("position", 0)
            if not program.link() or not program.bind() or not buffer.create() or not buffer.bind():
                raise GpuOperationError("scientific shader/buffer initialization failed: " + program.log())
        else:
            program, buffer = resources.program("image", vertex, fragment)
        functions.glUniform1i(program.uniformLocation("pixels"), 0)
        doubles.glUniform1d(program.uniformLocation("deviceRatio"), extent.dpr)
        doubles.glUniform1d(program.uniformLocation("targetHeight"), float(extent.pixel_height))
        check("sampler uniform")
        functions.glDisable(0x0B71)  # depth
        functions.glDisable(0x0B90)  # stencil
        functions.glDisable(0x0B44)  # culling: negative waterfall height remains valid
        functions.glDisable(0x0BD0)  # dithering
        functions.glEnable(0x0BE2)   # premultiplied-alpha source-over, no unpremultiply round trip
        functions.glBlendFuncSeparate(1, 0x0303, 1, 0x0303)
        functions.glEnable(0x0C11)
        for layer in bundle.layers:
            if layer.image is None:
                continue
            if layer.image.width() > 16384 or layer.image.height() > 16384:
                raise ValueError("scientific texture exceeds prototype dimension bound")
            # One upload texture at a time; no retained history/cache or mipmaps.
            if resources is None:
                texture = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
                texture.setFormat(QOpenGLTexture.TextureFormat.RGBA8_UNorm)
                texture.setSize(layer.image.width(), layer.image.height())
                texture.setMipLevels(1)
                texture.setAutoMipMapGenerationEnabled(False)
                texture.allocateStorage(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.UInt8)
                texture.setData(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.UInt8, layer.image.constBits())
                texture.setMinMagFilters(QOpenGLTexture.Filter.Nearest, QOpenGLTexture.Filter.Nearest)
                texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
                texture.bind(0)
            else:
                texture = resources.texture(layer.name, layer.image)
            check("texture")
            functions.glUniform1f(program.uniformLocation("layerOpacity"), float(layer.opacity))
            doubles.glUniform4d(program.uniformLocation("pixelRect"), *layer.target_rect())
            functions.glUniform1i(program.uniformLocation("qtNearest"), int(layer.sampling == "qt611-nearest"))
            functions.glUniform1i(program.uniformLocation("spanStart"), sampled_image_scissor(layer, extent)[0])
            if layer.sampling == "qt611-nearest":
                doubles.glUniform4d(program.uniformLocation("qtInverse"),
                                    *qt_sampling_inverse(layer, extent))
            check("texture and opacity")
            data = texture_vertices().tobytes()
            if resources is None:
                buffer.allocate(data, len(data))
            else:
                resources.write("image", data)
            program.enableAttributeArray(0)
            program.setAttributeBuffer(0, 0x1406, 0, 2, 8)
            functions.glScissor(*sampled_image_scissor(layer, extent))
            functions.glDrawArrays(0x0005, 0, 4)
            check("draw")
            uploads += 1
            max_texture_bytes = max(max_texture_bytes, layer.image.width() * layer.image.height() * 4)
            texture.release()
            if resources is None:
                texture.destroy()
            texture = None
        program.disableAttributeArray(0)
        buffer.release()
        program.release()
        functions.glDisable(0x0C11)
        vectors = draw_vectors_gpu(functions, extent, bundle, resources=resources) if curves else None
        error = functions.glGetError()
        if error:
            raise GpuOperationError(f"scientific GL error {error}")
        return dict(texture_uploads=uploads, maximum_texture_bytes=max_texture_bytes,
                    vertex_buffer_bytes=32, live_textures_after=0 if resources is None else resources.snapshot()["live_textures"],
                    all_layers=False, vectors=vectors)
    finally:
        if texture is not None:
            texture.release()
            if resources is None:
                texture.destroy()
        if buffer.isCreated():
            buffer.release()
            if resources is None:
                buffer.destroy()
        if program.isLinked():
            program.release()
        if resources is None:
            program.removeAllShaders()
