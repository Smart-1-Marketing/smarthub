"""Provider contracts, real image validation and background workflow regressions."""
import base64
import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch, MagicMock

_tmp = tempfile.TemporaryDirectory(prefix="background-test-", ignore_cleanup_errors=True)
os.environ["HUB_DATA_DIR"] = _tmp.name
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp.name, "test.db")
os.environ["OPENAI_API_KEY"] = "test-only"

from PIL import Image
from modules.bg_remover import providers as p
from modules.bg_remover import app as bg
from modules.image_creator import app as creator
from hub import ai


def png(transparent=False):
    im = Image.new("RGBA", (80, 60), (0, 0, 255, 255))
    if transparent:
        im.putpixel((0, 0), (0, 0, 0, 0))
    out = io.BytesIO(); im.save(out, "PNG"); return out.getvalue()


def response(status=200, data=None):
    r = MagicMock(status_code=status)
    r.__enter__.return_value = r
    r.iter_content.return_value = [png(True) if data is None else data]
    return r


class ProviderTests(unittest.TestCase):
    def test_validate_bytes_and_transparency(self):
        for raw in (b"", b"not an image", b"%PDF-1.7"):
            with self.assertRaises(p.BackgroundError): p.normalize(raw)
        with self.assertRaises(p.BackgroundError): p.validate_output(png(), cutout=True)
        self.assertEqual(p.validate_output(png(True), cutout=True), png(True))
        self.assertEqual(p.validate_output(png(), cutout=False), png())

    def test_exif_is_upright_before_provider(self):
        im = Image.new("RGB", (80, 60)); exif=im.getexif(); exif[274]=6
        out=io.BytesIO(); im.save(out,"JPEG",exif=exif)
        self.assertEqual(Image.open(io.BytesIO(p.normalize(out.getvalue()))).size,(60,80))

    def test_cloud_pending_retries_same_asset(self):
        with patch.object(p,"cloud_ready",return_value=True), \
             patch("hub.storage.put",return_value=SimpleNamespace(backend="cloudinary",public_id="source")) as put, \
             patch("cloudinary.utils.cloudinary_url",return_value=("https://res.cloudinary.com/test/image/upload/result.png",{})) as url, \
             patch.object(p.requests,"get",side_effect=[response(423),response()]) as get, \
             patch.object(p.time,"sleep"), patch("hub.quotas.record_asset") as usage:
            self.assertEqual(p.cloud_cutout(png()),png(True))
            self.assertEqual(put.call_count,1); self.assertEqual(get.call_count,2)
            self.assertTrue(url.call_args.kwargs["sign_url"])
            self.assertEqual(url.call_args.kwargs["transformation"],[{"effect":"background_removal"}])
            self.assertEqual(usage.call_count,1)

    def test_cloud_failure_is_safe_and_not_success(self):
        for status in (403,500,423):
            with self.subTest(status=status), patch.object(p,"cloud_ready",return_value=True), \
                 patch("hub.storage.put",return_value=SimpleNamespace(backend="cloudinary",public_id="source")), \
                 patch("cloudinary.utils.cloudinary_url",return_value=("https://example.test/image",{})), \
                 patch.object(p.requests,"get",return_value=response(status)), patch.object(p.time,"sleep"), \
                 patch("hub.quotas.record_asset") as usage:
                with self.assertRaises(p.BackgroundError): p.cloud_cutout(png())
                usage.assert_not_called()

    def test_saved_links_are_account_scoped_and_never_redirect(self):
        with patch("hub.storage._configure"), patch("cloudinary.config",return_value=SimpleNamespace(cloud_name="our-cloud")), \
             patch.object(p.requests,"get",return_value=response()) as get:
            for url in ("http://127.0.0.1/a", "https://res.cloudinary.com/other/image/upload/a.png", "https://res.cloudinary.com@evil.test/our-cloud/image/upload/a.png", "https://res.cloudinary.com/our-cloud/image/upload/../../other/a.png"):
                with self.assertRaises(p.BackgroundError): p.saved_image(url)
            get.assert_not_called()
            p.saved_image("https://res.cloudinary.com/our-cloud/image/upload/a.png")
            self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_openai_sends_reference_and_counts_only_complete_responses(self):
        r=Mock(status_code=200)
        with patch.object(ai,"ready",return_value=True), patch.object(ai.requests,"post",return_value=r) as post, patch.object(ai,"_record") as record:
            for payload in ({"data":[]},{"data":[{"b64_json":"!!"}]},{"data":[{"b64_json":""}]}):
                r.json.return_value=payload; record.reset_mock()
                with self.assertRaises(ai.AIUnavailable): ai.image_edit("scene",png(),module="bg_remover",purpose="background_replace")
                self.assertFalse(record.call_args.args[5])
            r.json.return_value={"data":[{"b64_json":base64.b64encode(png()).decode()}]}
            self.assertEqual(ai.image_edit("scene",png(),module="bg_remover",purpose="background_replace"),[png()])
            self.assertTrue(post.call_args.args[0].endswith("/images/edits"))
            self.assertEqual(post.call_args.kwargs["files"]["image"][1],png())
            self.assertTrue(record.call_args.args[5])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.client=bg.app.test_client()
        self.stack=[]
        for target, value in (("configured",True),("_sweep",None),("_cache_get",None),("_cache_put",None),("_log",None)):
            patcher=patch.object(bg,target,return_value=value); self.stack.append(patcher); patcher.start()
        self.addCleanup(patch.stopall)

    def send(self,**data):
        return self.client.post("/api/remove",data={"images":(io.BytesIO(png()),"fixture.png"),**data})

    def test_modes_prompt_and_invalid_file_fail_before_paid_call(self):
        with patch.object(p,"cloud_cutout") as call:
            legacy = self.send(quality="preview")
            self.assertEqual(legacy.status_code,400)
            self.assertIn("Refresh",legacy.json["error"])
            self.assertEqual(self.send(mode="wrong").status_code,400)
            self.assertEqual(self.send(mode="replace").status_code,400)
            r=self.client.post("/api/remove",data={"images":(io.BytesIO(b"bad"),"test.png")})
            self.assertEqual(r.status_code,400); call.assert_not_called()

    def test_cache_separates_prompts_and_modes_but_reuses_resizing(self):
        with patch.object(p,"ai_ready",return_value=True),patch.object(p,"cloud_cutout",return_value=png(True)),patch.object(p,"replace_background",return_value=png()):
            ids=[]
            for mode,prompt in (("cutout",""),("replace","kitchen"),("replace","beach")):
                r=self.send(mode=mode,prompt=prompt); self.assertEqual(r.status_code,200);ids.append(r.json["results"][0]["id"])
            self.assertEqual(len(set(ids)),3)
            r=self.send(mode="cutout",post_resize="800")
            self.assertEqual(r.json["results"][0]["id"],ids[0])

    def test_saved_link_and_logo_route_use_cloudinary(self):
        with patch.object(p,"saved_image",return_value=png()),patch.object(p,"cloud_cutout",return_value=png(True)) as call:
            r=self.client.post("/api/remove",data={"image_url":"https://saved.example.test/fixture.png"})
            self.assertEqual(r.status_code,200)
            r=creator.app.test_client().post("/api/logos/remove-background",json={"image":"data:image/png;base64,"+base64.b64encode(png()).decode()})
            self.assertEqual(r.status_code,200);self.assertEqual(call.call_count,2)

    def test_page_has_no_obsolete_credit_promise(self):
        html=self.client.get("/").get_data(as_text=True)
        self.assertIn('value="replace"',html)
        self.assertIn('id="imageUrl"',html)
        self.assertNotIn('free preview',html)
        self.assertNotIn('1 credit',html)


if __name__ == "__main__": unittest.main()
