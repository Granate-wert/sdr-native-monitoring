"""Experimental stencil stroke fill and coverage pattern shader, no wide lines."""
from dataclasses import replace
from math import floor
from scripts.app05_gpu_errors import GpuOperationError


def draw_vectors_gpu(functions, extent, bundle, *, resources=None):
    from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
    from scripts.app05_scientific_gpu import image_scissor, physical_scissor, texture_vertices
    from scripts.app05_vector_geometry import normalized_polygon, pattern_rows, stroke_polygons
    program = QOpenGLShaderProgram()
    buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
    quad = texture_vertices().tobytes()
    patterns: dict[int, tuple[int, ...]] = {}
    strokes = []
    coverage_count = 0
    try:
        vertex = """#version 400
            in vec2 position; void main(){gl_Position=vec4(position,0.,1.);}"""
        fragment = """#version 400
            uniform vec4 color; uniform int patterned; uniform ivec4 rowsA; uniform ivec4 rowsB;
            uniform ivec2 patternOrigin; uniform float targetHeight;
            out vec4 fragmentColor;
            void main(){
                if(patterned!=0){
                    ivec2 p=ivec2(floor(vec2(gl_FragCoord.x,targetHeight-gl_FragCoord.y)))-patternOrigin;
                    p=((p%8)+8)%8;
                    int bits=p.y<4?rowsA[p.y]:rowsB[p.y-4];
                    if(((bits>>p.x)&1)==0)discard;
                }
                fragmentColor=color;
            }"""
        if resources is None:
            for kind, source in ((QOpenGLShader.ShaderTypeBit.Vertex, vertex), (QOpenGLShader.ShaderTypeBit.Fragment, fragment)):
                if not program.addShaderFromSourceCode(kind, source):
                    raise GpuOperationError(program.log())
            program.bindAttributeLocation("position", 0)
            if not program.link() or not program.bind() or not buffer.create() or not buffer.bind():
                raise GpuOperationError("vector shader initialization failed: " + program.log())
        else:
            program, buffer = resources.program("vector", vertex, fragment)
        program.enableAttributeArray(0)
        functions.glUniform1f(program.uniformLocation("targetHeight"), float(extent.pixel_height))
        functions.glDisable(0x0B71)
        functions.glDisable(0x0B44)
        functions.glDisable(0x0BD0)
        functions.glEnable(0x0C11)
        functions.glEnable(0x0BE2)
        functions.glBlendFuncSeparate(1, 0x0303, 1, 0x0303)

        def draw(data, primitive, count):
            if resources is None:
                buffer.allocate(data, len(data))
            else:
                resources.write("vector", data)
            program.setAttributeBuffer(0, 0x1406, 0, 2, 8)
            functions.glDrawArrays(primitive, 0, count)

        def color(rgba, opacity):
            r, g, b, a = rgba
            alpha = a / 255 * opacity
            functions.glUniform4f(program.uniformLocation("color"), r/255*alpha, g/255*alpha, b/255*alpha, alpha)

        for layer in bundle.layers:
            if layer.image is not None:
                continue
            functions.glUniform2i(program.uniformLocation("patternOrigin"),
                                 floor(layer.clip[0]*extent.dpr), floor(layer.clip[1]*extent.dpr))
            if layer.coverage:
                functions.glDisable(0x0B90)
                functions.glUniform1i(program.uniformLocation("patterned"), 1)
                for rect in layer.coverage:
                    if rect.style not in patterns:
                        if len(patterns) >= 4:
                            raise ValueError("coverage pattern bound exceeded")
                        patterns[rect.style] = pattern_rows(rect.style)
                    mask = patterns[rect.style]
                    functions.glUniform4i(program.uniformLocation("rowsA"), *mask[:4])
                    functions.glUniform4i(program.uniformLocation("rowsB"), *mask[4:])
                    color(rect.rgba, layer.opacity)
                    functions.glScissor(*image_scissor(replace(layer, local_rect=rect.rect), extent))
                    draw(quad, 0x0005, 4)
                    coverage_count += 1
                continue
            if layer.path is None:
                raise ValueError("unknown scientific vector layer")
            # One outline's transient geometry at a time, not retained in bundle.
            retained_gpu = resources.live_bytes if resources is not None else 0
            geometry_budget = min(32*1024*1024, extent.target_budget_bytes-extent.nominal_target_bytes-bundle.retained_bytes-retained_gpu)
            polygons, metric = stroke_polygons(layer, extent, geometry_budget)
            strokes.append(dict(name=layer.name, **metric))
            functions.glScissor(*physical_scissor(layer.clip, extent))
            functions.glUniform1i(program.uniformLocation("patterned"), 0)
            functions.glEnable(0x0B90)
            functions.glStencilMask(255)
            functions.glClearStencil(0)
            functions.glClear(0x0400)
            functions.glColorMask(False, False, False, False)
            functions.glStencilFunc(0x0207, 0, 255)  # ALWAYS
            functions.glStencilOpSeparate(0x0404, 0x1E00, 0x1E00, 0x8507)  # front INCR_WRAP
            functions.glStencilOpSeparate(0x0405, 0x1E00, 0x1E00, 0x8508)  # back DECR_WRAP
            for polygon in polygons:
                if len(polygon) >= 3:
                    vertices = normalized_polygon(polygon, extent)
                    draw(vertices.tobytes(), 0x0006, len(vertices))
                    del vertices
            polygon = None  # release last Qt polygon before preparing next stroke
            functions.glColorMask(True, True, True, True)
            functions.glStencilFunc(0x0205, 0, 255)  # NOTEQUAL: nonzero winding
            functions.glStencilOp(0x1E00, 0x1E00, 0)
            color(layer.pen.color().getRgb(), layer.opacity)
            draw(quad, 0x0005, 4)
            functions.glDisable(0x0B90)
            del polygons
        error = functions.glGetError()
        if error:
            raise GpuOperationError(f"scientific vector GL error {error}")
        return dict(strokes=strokes, coverage_rectangles=coverage_count,
                    pattern_anchor="plot-top-left physical pixels; 8x8 cosmetic tile",
                    peak_geometry_bytes=max((s["nominal_geometry_bytes"] for s in strokes), default=0))
    finally:
        functions.glColorMask(True, True, True, True)
        functions.glDisable(0x0B90)
        functions.glDisable(0x0C11)
        if program.isLinked():
            program.disableAttributeArray(0)
            program.release()
        if buffer.isCreated():
            buffer.release()
            if resources is None:
                buffer.destroy()
        if resources is None:
            program.removeAllShaders()
