"""Native texture shader plus stencil curves/coverage for detached scientific layers.

Experimental bounded resources, ephemeral by default or explicitly context-owned.
Never wired into product. Full uploads, not a throughput benchmark. No GL wide lines.
"""
from math import ceil


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


def draw_scientific_gpu(device, functions, extent, bundle, *, curves=True, resources=None):
    from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture, QOpenGLFunctions_4_0_Core
    from scripts.app05_vector_gpu import draw_vectors_gpu
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
        raise RuntimeError("scientific texel prototype requires OpenGL 4.0; select CPU fallback")
    program = QOpenGLShaderProgram()
    buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
    texture = None
    uploads = 0
    max_texture_bytes = 0
    def check(stage):
        error = functions.glGetError()
        if error:
            raise RuntimeError(f"scientific GL error {error} at {stage}")
    try:
        check("entry")
        vertex = """#version 400
            in vec2 position; void main(){gl_Position=vec4(position,0.,1.);}"""
        fragment = """#version 400
            uniform sampler2D pixels; uniform float layerOpacity;
            uniform dvec4 pixelRect; uniform double deviceRatio; uniform double targetHeight;
            out vec4 fragmentColor;
            void main(){
                precise dvec2 point=dvec2(gl_FragCoord.x,targetHeight-double(gl_FragCoord.y))/deviceRatio;
                precise dvec2 samplePosition=(point-pixelRect.xy)*dvec2(textureSize(pixels,0))/pixelRect.zw;
                ivec2 index=clamp(ivec2(floor(samplePosition)),ivec2(0),textureSize(pixels,0)-ivec2(1));
                fragmentColor=texelFetch(pixels,index,0)*layerOpacity;
            }"""
        if resources is None:
            if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex):
                raise RuntimeError(program.log())
            if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment):
                raise RuntimeError(program.log())
            program.bindAttributeLocation("position", 0)
            if not program.link() or not program.bind() or not buffer.create() or not buffer.bind():
                raise RuntimeError("scientific shader/buffer initialization failed: " + program.log())
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
            check("texture and opacity")
            data = texture_vertices().tobytes()
            if resources is None:
                buffer.allocate(data, len(data))
            else:
                resources.write("image", data)
            program.enableAttributeArray(0)
            program.setAttributeBuffer(0, 0x1406, 0, 2, 8)
            functions.glScissor(*image_scissor(layer, extent))
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
            raise RuntimeError(f"scientific GL error {error}")
        return dict(texture_uploads=uploads, maximum_texture_bytes=max_texture_bytes,
                    vertex_buffer_bytes=32, live_textures_after=0 if resources is None else resources.snapshot()["live_textures"],
                    all_layers=False, vectors=vectors)
    finally:
        if texture is not None:
            texture.release()
            if resources is None:
                texture.destroy()
        buffer.release()
        if resources is None:
            buffer.destroy()
        program.release()
        if resources is None:
            program.removeAllShaders()
