"""GPU pipeline failures must not become reusable partially uploaded frames."""
import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor
from PySide6.QtOpenGL import QOpenGLShaderProgram, QOpenGLTexture, QOpenGLBuffer, QOpenGLFramebufferObject
from PySide6.QtWidgets import QApplication

from scripts.app05_scene_gpu_support import SceneExtent, SceneGpuTarget, compare_images, image_target
from scripts.app05_scientific_gpu import draw_scientific_gpu
from scripts.app05_gpu_errors import GpuOperationError
from tests.ui_v2 import test_app05_gpu_resources as fixtures


class GpuFailureTests(unittest.TestCase):
    bundle = fixtures.PersistentGpuTests.bundle
    render = fixtures.PersistentGpuTests.render

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.target = SceneGpuTarget()
        self.addCleanup(self.target.close)
        if not self.target.available:
            self.skipTest("requires native GL context")

    def assert_failure_falls_back(self, owner_type, method, *, result=None, failure=None):
        bundle = self.bundle()
        attempts = []
        def draw(device, functions, extent):
            attempts.append(1)
            draw_scientific_gpu(device, functions, extent, bundle, resources=self.target.scientific_resources())
        options = {"side_effect": failure} if failure is not None else {"return_value": result}
        with patch.object(owner_type, method, **options):
            actual, status = self.target.render_or_cpu(SceneExtent(80, 64), [], QColor("blue"), draw=draw)
        self.assertEqual(status["backend"], "cpu")
        self.assertTrue(compare_images(image_target(SceneExtent(80, 64), QColor("blue")), actual)["equal"])
        report = self.target.snapshot()
        self.assertEqual(report["live_targets"], 0)
        self.assertEqual(report["scientific_resources"]["live_bytes"], 0)
        self.assertTrue(report["scientific_resources"]["closed"])
        current, status = self.target.render_or_cpu(SceneExtent(80, 64), [], QColor("green"), draw=draw)
        self.assertEqual(status["backend"], "cpu")
        self.assertEqual(current.pixelColor(10, 10), QColor("green"))
        self.assertEqual(len(attempts), 1, "GPU failure must stay latched until explicit recovery")
        self.target.recover_context()
        self.assertEqual(self.render(bundle).pixelColor(10, 10), QColor("red"))

    def test_shader_compile_failure_releases_partial_resources_and_latches_cpu(self):
        self.assert_failure_falls_back(QOpenGLShaderProgram, "addShaderFromSourceCode", result=False)

    def test_link_failure_releases_partial_resources_and_latches_cpu(self):
        self.assert_failure_falls_back(QOpenGLShaderProgram, "link", result=False)

    def test_buffer_creation_failure_releases_partial_resources_and_latches_cpu(self):
        self.assert_failure_falls_back(QOpenGLBuffer, "create", result=False)

    def test_texture_upload_failure_does_not_leave_reusable_old_frame(self):
        self.render(self.bundle("green"))
        self.assert_failure_falls_back(QOpenGLTexture, "setData", failure=RuntimeError("injected upload failure"))

    def test_buffer_write_failure_does_not_leave_reusable_geometry(self):
        self.render(self.bundle("green"))
        self.assert_failure_falls_back(QOpenGLBuffer, "write", failure=RuntimeError("injected buffer write failure"))

    def test_texture_allocation_failure_releases_partial_storage(self):
        self.assert_failure_falls_back(QOpenGLTexture, "isStorageAllocated", result=False)

    def test_buffer_allocation_failure_releases_partial_storage(self):
        self.assert_failure_falls_back(QOpenGLBuffer, "size", result=-1)

    def test_program_bind_failure_releases_linked_program(self):
        self.assert_failure_falls_back(QOpenGLShaderProgram, "bind", result=False)

    def test_vector_init_failure_after_image_upload_cleans_both_slots(self):
        original = QOpenGLShaderProgram.addShaderFromSourceCode
        calls = []
        def fail_vector(program, kind, source):
            calls.append(1)
            return False if len(calls) == 3 else original(program, kind, source)
        with patch.object(QOpenGLShaderProgram, "addShaderFromSourceCode", fail_vector):
            with self.assertRaises(GpuOperationError):
                self.render(self.bundle())
        self.assertEqual(len(calls), 3)
        report = self.target.snapshot()
        self.assertEqual(report["live_targets"], 0)
        self.assertEqual(report["scientific_resources"]["live_bytes"], 0)

    def test_real_gl_invalid_enum_is_detected_and_latched(self):
        def draw(device, functions, extent):
            functions.glEnable(0xFFFFFFFF)  # actual GL_INVALID_ENUM, not mocked glGetError
        _, status = self.target.render_or_cpu(SceneExtent(80, 64), [], QColor("blue"), draw=draw)
        self.assertEqual(status["backend"], "cpu")
        self.assertIn("1280", status["reason"])
        self.assertEqual(self.target.snapshot()["live_targets"], 0)
        self.target.recover_context()
        self.assertEqual(self.render(self.bundle()).pixelColor(10, 10), QColor("red"))

    def test_swallowed_resource_error_cannot_publish_partial_frame(self):
        def draw(device, functions, extent):
            owner = self.target.scientific_resources()
            owner.set_budget(1024)
            with patch.object(QOpenGLShaderProgram, "addShaderFromSourceCode", return_value=False):
                with self.assertRaises(GpuOperationError):
                    owner.program("image", "bad", "bad")
            with self.assertRaises(GpuOperationError):
                owner.set_budget(1024)
        _, status = self.target.render_or_cpu(SceneExtent(80, 64), [], QColor("blue"), draw=draw)
        self.assertEqual(status["backend"], "cpu")
        self.assertEqual(self.target.snapshot()["live_targets"], 0)

    def test_validation_and_admission_errors_are_not_hidden(self):
        for error in (ValueError("invalid input"), MemoryError("admission budget"), RuntimeError("caller bug")):
            with self.subTest(error=type(error).__name__):
                def draw(device, functions, extent):
                    raise error
                with self.assertRaises(type(error)):
                    self.target.render_or_cpu(SceneExtent(80, 64), [], QColor("blue"), draw=draw)
                self.assertIsNone(self.target.snapshot()["context_failure"])
        self.assertEqual(self.render(self.bundle()).pixelColor(10, 10), QColor("red"))

    def test_ephemeral_shader_failure_cleanup_is_safe_too(self):
        with patch.object(QOpenGLShaderProgram, "addShaderFromSourceCode", return_value=False):
            with self.assertRaises(GpuOperationError):
                self.render(self.bundle(), persistent=False)
        self.assertEqual(self.target.snapshot()["live_targets"], 0)
        self.target.recover_context()
        self.assertEqual(self.render(self.bundle(), persistent=False).pixelColor(10, 10), QColor("red"))

    def test_framebuffer_bind_failure_cannot_return_stale_pixels(self):
        self.render(self.bundle("green"))
        with patch.object(QOpenGLFramebufferObject, "bind", return_value=False):
            image, status = self.target.render_or_cpu(SceneExtent(80, 64), [], QColor("blue"))
        self.assertEqual(status["backend"], "cpu")
        self.assertEqual(image.pixelColor(10, 10), QColor("blue"))
        self.assertEqual(self.target.snapshot()["live_targets"], 0)

    def test_failed_cleanup_is_observable_until_context_retired(self):
        self.render(self.bundle())
        owner = self.target._resources
        with patch.object(owner, "close", side_effect=RuntimeError("injected cleanup failure")):
            def draw(device, functions, extent):
                raise GpuOperationError("injected draw failure")
            _, status = self.target.render_or_cpu(SceneExtent(80, 64), [], QColor("blue"), draw=draw)
        self.assertEqual(status["backend"], "cpu")
        failed = self.target.snapshot()
        self.assertEqual(failed["live_targets"], 1)
        self.assertFalse(failed["scientific_resources"]["closed"])
        self.assertIn("injected cleanup failure", failed["destruction_cleanup_error"])
        self.target.recreate_context()
        self.assertIsNone(self.target.snapshot()["destruction_cleanup_error"])
        self.assertEqual(self.render(self.bundle()).pixelColor(10, 10), QColor("red"))


if __name__ == "__main__":
    unittest.main()
