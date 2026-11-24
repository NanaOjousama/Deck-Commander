import importlib.util
from pathlib import Path
import os
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'service/render_service.py'
spec=importlib.util.spec_from_file_location('service_renderer', SCRIPT)
renderer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='dc-service-render-')
        self.addCleanup(self.tmp.cleanup)
        self.app=Path(self.tmp.name)/'app'
        self.app.mkdir()
        self.connector=self.app/'connector'
        self.connector.write_text('#!/bin/sh\nexit 77\n')
        self.connector.chmod(0o755)

    def valid(self):
        return renderer.render(str(self.app),'dc_test','dc-test-state',str(self.connector))

    def test_secure_template_properties_and_connector_parent(self):
        unit=self.valid()
        for value in ('ExecStart='+str(self.connector),'User=dc_test','Group=dc_test',
                      'KillMode=control-group','Restart=no','StateDirectoryMode=0700',
                      'ProtectHome=true','ProtectSystem=strict','NoNewPrivileges=true',
                      'DC_REQUIRE_TOOL_ALLOWLIST=1','DC_STRICT_DEPLOYMENT=1',
                      'StateDirectory=dc-test-state'):
            self.assertIn(value,unit)
        self.assertNotIn('ExecStart='+str(self.app/'gateway.mjs'),unit)
        self.assertNotIn('@',unit)

    def test_rejects_shell_directives_and_unsafe_directory_names(self):
        for bad in ('dc test','dc;rm','dc@bad','../dc','root'):
            with self.subTest(value=bad):
                if bad=='root':continue
                with self.assertRaises(ValueError):
                    renderer.render(str(self.app),bad,'dc-test-state',str(self.connector))
        with self.assertRaises(ValueError):
            renderer.render(str(self.app),'dc_test','foo/../bar',str(self.connector))
        with self.assertRaises(ValueError):
            renderer.render(str(self.app),'dc_test','dc-test',str(self.connector)+';echo')
        with self.assertRaises(ValueError):
            renderer.render(str(self.app),'dc_test','dc-test',str(self.app/'missing'))

    def test_output_creation_is_never_an_install_action(self):
        self.assertFalse(Path(self.tmp.name,'/etc/systemd/system/deck-commander.service').exists())
        unit=self.valid()
        self.assertTrue(unit.startswith('[Unit]'))
        self.assertTrue((Path(self.tmp.name)/'app').exists())


if __name__=='__main__':unittest.main()
