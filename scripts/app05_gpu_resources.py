"""Context-local bounded GPU objects, no source publications or CPU image cache.

Experimental owner under SceneGpuTarget. Programs and storage are reused;
every draw uploads current bytes, so object reuse never implies data identity.
"""
import threading

from PySide6.QtGui import QImage, QOpenGLContext
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture


class ScientificGpuResources:
    def __init__(self):
        self._context = QOpenGLContext.currentContext()
        if self._context is None:
            raise RuntimeError("GPU resources require current owning context")
        self._thread = threading.get_ident()
        self._programs = {}
        self._buffers = {}
        self._capacities = {}
        self._textures = {}
        self._closed = False
        self._budget = 0
        self.program_builds = self.buffer_allocations = self.texture_allocations = 0
        self.texture_releases = self.uploads = 0
        self.abandoned_bytes = self.abandoned_textures = 0

    def _guard(self):
        if self._closed:
            raise RuntimeError("GPU resources are closed")
        if threading.get_ident() != self._thread or QOpenGLContext.currentContext() != self._context:
            raise RuntimeError("GPU resources require owning thread and current context")

    @property
    def live_bytes(self):
        return sum(self._capacities.values()) + sum(w*h*4 for _, w, h in self._textures.values())

    def set_budget(self, budget):
        self._guard()
        if budget < self.live_bytes:
            raise MemoryError("retained scientific GPU storage exceeds new budget")
        self._budget = budget

    def program(self, key, vertex, fragment):
        self._guard()
        if key not in ("image", "vector"):
            raise ValueError("unknown scientific program slot")
        if key not in self._programs:
            program = QOpenGLShaderProgram()
            buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            try:
                for kind, source in ((QOpenGLShader.ShaderTypeBit.Vertex, vertex),
                                     (QOpenGLShader.ShaderTypeBit.Fragment, fragment)):
                    if not program.addShaderFromSourceCode(kind, source):
                        raise RuntimeError(program.log())
                program.bindAttributeLocation("position", 0)
                if not program.link() or not buffer.create():
                    raise RuntimeError("persistent scientific program initialization failed: " + program.log())
            except BaseException:
                buffer.destroy()
                program.removeAllShaders()
                raise
            self._programs[key], self._buffers[key], self._capacities[key] = program, buffer, 0
            self.program_builds += 1
        program, buffer = self._programs[key], self._buffers[key]
        if not program.bind() or not buffer.bind():
            raise RuntimeError("persistent scientific program/buffer bind failed")
        return program, buffer

    def write(self, key, data):
        self._guard()
        required = len(data)
        capacity = self._capacities[key]
        if required > 32*1024*1024 or self.live_bytes-capacity+max(capacity, required) > self._budget:
            raise MemoryError("persistent scientific buffer budget exceeded")
        buffer = self._buffers[key]
        if not buffer.bind():
            raise RuntimeError("persistent scientific buffer bind failed")
        if required > capacity:
            # Reallocate existing object's storage; CPU payload is not retained.
            buffer.allocate(required)
            if buffer.size() != required:
                raise MemoryError("persistent scientific buffer allocation failed")
            self._capacities[key] = required
            self.buffer_allocations += 1
        buffer.write(0, data, required)

    def texture(self, key, image):
        self._guard()
        if key not in ("persistence", "waterfall-0", "waterfall-1"):
            raise ValueError("unknown scientific texture slot")
        if image.format() != QImage.Format.Format_RGBA8888_Premultiplied:
            raise ValueError("scientific texture requires premultiplied RGBA8 bytes")
        width, height = image.width(), image.height()
        if not 0 < width <= 16384 or not 0 < height <= 16384:
            raise ValueError("scientific texture dimension bound exceeded")
        previous = self._textures.get(key)
        if previous is not None and previous[1:] != (width, height):
            self._drop_texture(key)
            previous = None
        if previous is None:
            if self.live_bytes + width*height*8 > self._budget:
                raise MemoryError("persistent scientific texture/upload budget exceeded")
            texture = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
            try:
                texture.setFormat(QOpenGLTexture.TextureFormat.RGBA8_UNorm)
                texture.setSize(width, height)
                texture.setMipLevels(1)
                texture.setAutoMipMapGenerationEnabled(False)
                texture.allocateStorage(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.UInt8)
                if not texture.isStorageAllocated():
                    raise MemoryError("persistent scientific texture allocation failed")
                texture.setMinMagFilters(QOpenGLTexture.Filter.Nearest, QOpenGLTexture.Filter.Nearest)
                texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
            except BaseException:
                texture.destroy()
                raise
            self._textures[key] = texture, width, height
            self.texture_allocations += 1
        else:
            texture = previous[0]
            if self.live_bytes + width*height*4 > self._budget:
                raise MemoryError("persistent scientific texture upload budget exceeded")
        texture.setData(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.UInt8, image.constBits())
        texture.bind(0)
        self.uploads += 1
        return texture

    def _drop_texture(self, key):
        texture, _, _ = self._textures.pop(key)
        texture.destroy()
        self.texture_releases += 1

    def close(self):
        if self._closed:
            return
        self._guard()
        for key in tuple(self._textures):
            self._drop_texture(key)
        for buffer in self._buffers.values():
            buffer.destroy()
        for program in self._programs.values():
            program.release()
            program.removeAllShaders()
        self._buffers.clear()
        self._capacities.clear()
        self._programs.clear()
        self._closed = True

    def abandon_destroyed_context(self):
        """Drop wrappers only AFTER the owning QObject destroyed signal.

        No explicit GL delete/release calls and no foreign current context.
        Native context teardown owns these objects; do not count as close().
        """
        from shiboken6 import isValid
        if self._closed:
            return
        if threading.get_ident() != self._thread or isValid(self._context):
            raise RuntimeError("cannot abandon a live context or from another thread")
        if QOpenGLContext.currentContext() is not None:
            raise RuntimeError("cannot abandon with a foreign current context")
        self.abandoned_bytes = self.live_bytes
        self.abandoned_textures = len(self._textures)
        self._textures.clear()
        self._buffers.clear()
        self._capacities.clear()
        self._programs.clear()
        self._closed = True

    def snapshot(self):
        return dict(program_builds=self.program_builds, buffer_allocations=self.buffer_allocations,
            texture_allocations=self.texture_allocations, texture_releases=self.texture_releases,
            uploads=self.uploads, live_bytes=self.live_bytes, live_textures=len(self._textures),
            abandoned_bytes=self.abandoned_bytes, abandoned_textures=self.abandoned_textures,
            live_programs=len(self._programs), closed=self._closed,
            scope="nominal GL storage, excludes opaque driver/program/Qt allocations; no CPU sources retained")
