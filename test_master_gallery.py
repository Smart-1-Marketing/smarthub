"""Client asset home: isolation, complete search, and legacy project coverage."""
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory(prefix="master-gallery-", ignore_cleanup_errors=True)
os.environ["HUB_DATA_DIR"] = _temp.name
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_temp.name) / "test.db")
os.environ["HUB_SCHEDULER"] = "0"

from flask import Flask
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from modules.image_picker import catalog, app as picker
from modules.image_picker.models import Base, PickerClient, SavedImage


class MasterGalleryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.client = PickerClient(name="Example Co", slug="example-co", share_enabled=False)
        self.other = PickerClient(name="Example Co Supply", slug="example-co-supply")
        self.db.add_all([self.client, self.other]); self.db.commit()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for key in ("legacy_images", "project_sources", "commercial_projects", "proposal_projects"):
            self.stack.enter_context(patch.object(catalog, key, return_value=[]))
        self.stack.enter_context(patch.object(picker, "session", return_value=self.db))
        self.stack.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("No network")))
        self.app = Flask(__name__); self.app.secret_key = "local-test"
        self.app.register_blueprint(picker.bp)
        self.http = self.app.test_client()
        with self.http.session_transaction() as s: s["hub_user"] = "test"

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def asset(self, name="file.png", client=None, **extra):
        r = SavedImage(client_id=(client or self.client).id, provider="local", provider_image_id=name,
                       filename=name, cloudinary_url="https://example.test/" + name,
                       cloudinary_public_id="legacy/" + name, resource_type="image", **extra)
        self.db.add(r); self.db.commit(); return r

    def test_five_empty_sections_without_creating_or_enabling_sharing(self):
        out = catalog.catalog(self.db, self.client, self.client.name)
        self.assertEqual([s["key"] for s in out["sections"]],
                         ["uploads", "creative", "projects", "logos", "internal"])
        self.assertEqual(out["total"], 0)
        # The five folders are offered at zero, never dropped for being empty.
        self.assertEqual([f["label"] for f in out["folders"]],
                         ["Client Uploads", "Creative", "Hub Projects", "Logos", "Internal"])
        self.assertTrue(all(f["count"] == 0 for f in out["folders"]))
        self.assertTrue(out["optimization"]["measured"])
        self.assertEqual(out["optimization"]["total"], 0)
        self.assertFalse(self.client.share_enabled)
        self.assertEqual(len(self.db.scalars(select(PickerClient)).all()), 2)

    def test_logos_and_internal_are_their_own_sections(self):
        from hub import client_logos
        # hub/client_logos.py files under the label "Logo"; an upload panel
        # types "Logos"; a rep's Drive import is internal. One Logos folder.
        brand = catalog.organize({"collection_kind": "logo", "provider": "logo_brand",
                                  "collection_label": "Logo", "collection_key": "brand"})
        typed = catalog.organize({"collection_kind": "upload", "provider": "local",
                                  "collection_label": "logos", "project_name": "logos"})
        internal = catalog.organize({"collection_kind": "internal", "provider": "google_drive",
                                     "collection_label": "Store photos", "project_name": "Store photos"})
        self.assertEqual((brand["section"], brand["folder"]), ("logos", "Logos"))
        self.assertEqual((typed["section"], typed["folder"]), ("logos", "Logos"))
        self.assertEqual((internal["section"], internal["folder"]), ("internal", "Store photos"))
        self.assertEqual(client_logos.KIND, "logo")
        self.assertEqual(catalog.section_for_folder("LOGOS"), "logos")
        self.assertEqual(catalog.section_for_folder("Spring refresh"), "")

    def test_folder_index_lists_named_folders_after_the_defaults(self):
        self.asset("a.png", collection_kind="upload", collection_label="Spring refresh",
                   project_name="Spring refresh")
        self.asset("b.png", collection_kind="upload", collection_label="Spring refresh",
                   project_name="Spring refresh")
        self.asset("mark.png", collection_kind="internal", collection_label="Logos",
                   project_name="Logos")
        self.asset("private.png", self.other, collection_kind="upload",
                   collection_label="Their folder", project_name="Their folder")
        folders = catalog.folder_index(self.db, self.client)
        labels = [f["label"] for f in folders]
        self.assertEqual(labels[:5], ["Client Uploads", "Creative", "Hub Projects", "Logos", "Internal"])
        self.assertIn("Spring refresh", labels)
        self.assertNotIn("Their folder", labels)
        by = {f["label"]: f for f in folders}
        self.assertEqual(by["Spring refresh"]["count"], 2)
        self.assertEqual(by["Client Uploads"]["count"], 2)
        self.assertEqual(by["Logos"]["count"], 1)
        self.assertEqual(by["Internal"]["count"], 0)
        # A gallery that does not exist yet still offers the five.
        self.assertEqual([f["label"] for f in catalog.folder_index(self.db, None)],
                         ["Client Uploads", "Creative", "Hub Projects", "Logos", "Internal"])

    def test_folders_api_is_scoped_to_the_token_or_the_staff_session(self):
        self.asset("a.png", collection_kind="upload", collection_label="Spring refresh",
                   project_name="Spring refresh")
        self.other.share_enabled = True; self.db.commit()
        # A share token sees its own gallery's folders and nobody else's.
        anon = self.app.test_client()
        r = anon.get(f"/tools/image-picker/api/folders?t={self.other.share_token}")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("Spring refresh", [f["label"] for f in r.json["folders"]])
        # Sharing off means the token is dead for this too.
        self.assertEqual(anon.get(f"/tools/image-picker/api/folders?t={self.client.share_token}").status_code, 404)
        # Staff by id.
        r = self.http.get(f"/tools/image-picker/api/folders?client_id={self.client.id}")
        self.assertIn("Spring refresh", [f["label"] for f in r.json["folders"]])
        self.assertEqual(self.app.test_client().get(
            f"/tools/image-picker/api/folders?client_id={self.client.id}").status_code, 401)

    def test_catalog_carries_the_seo_copy_beside_the_original(self):
        from modules.image_picker import optimize
        from modules.image_picker.models import ImageOptimization
        row = self.asset("photo.jpg", collection_kind="upload")
        queued = optimize.enqueue(self.db, row)
        self.assertEqual(queued.state, "pending")
        self.assertEqual(optimize.enqueue(self.db, row).id, queued.id)   # idempotent
        out = catalog.catalog(self.db, self.client, self.client.name)
        self.assertEqual(out["optimization"]["pending"], 1)
        self.assertNotIn("optimized", out["images"][0])
        queued.state = "done"; queued.optimized_url = "https://example.test/photo-1.webp"
        queued.seo_filename = "blue-storefront.webp"; self.db.commit()
        out = catalog.catalog(self.db, self.client, self.client.name)
        self.assertEqual(out["images"][0]["optimized"]["url"], "https://example.test/photo-1.webp")
        self.assertEqual(out["images"][0]["url"], "https://example.test/photo.jpg")
        self.assertEqual(out["optimization"]["done"], 1)
        # Deleting the original takes the copy's row with it.
        with patch("modules.image_picker.cloudinary_sink.destroy", return_value=True) as gone:
            optimize.forget(self.db, row)
        self.db.commit()
        self.assertIsNone(self.db.get(ImageOptimization, queued.id))
        gone.assert_not_called()          # no public_id was stored for the copy

    def test_notices_reach_everyone_attached_and_nobody_else(self):
        from modules.image_picker import notices
        with patch("hub.client_owner.owner_of", return_value={"email": "Owner@Smart1.test"}), \
                patch("hub.client_owner.followers_of", return_value=[{"email": "fan@smart1.test"}, {"email": "owner@smart1.test"}]), \
                patch("hub.client_owner.client_success_of", return_value={"email": "cs@smart1.test"}):
            self.assertEqual(notices.attached("Example Co"),
                             ["owner@smart1.test", "fan@smart1.test", "cs@smart1.test"])
            registered = []
            with patch("hub.job_notify.register", side_effect=lambda **kw: registered.append(kw) or kw):
                n = notices.uploads_recorded("Example Co", count=3, by="client", folder="Logos")
        self.assertEqual(n, 3)
        self.assertEqual({r["owner"] for r in registered},
                         {"owner@smart1.test", "fan@smart1.test", "cs@smart1.test"})
        self.assertTrue(all(r["status"] == "done" for r in registered))
        self.assertIn("3 new files for Example Co into Logos", registered[0]["label"])
        self.assertIn("from the client", registered[0]["label"])
        self.assertIn("for-client?name=Example%20Co", registered[0]["return_url"])
        # The same hour re-registers the same pointer, so forty files is one card.
        with patch("hub.client_owner.owner_of", return_value={"email": "owner@smart1.test"}), \
                patch("hub.client_owner.followers_of", return_value=[]), \
                patch("hub.client_owner.client_success_of", return_value={}), \
                patch("hub.job_notify.register", side_effect=lambda **kw: registered.append(kw) or kw):
            notices.uploads_recorded("Example Co", count=40, by="staff")
        self.assertEqual(registered[-1]["id"], registered[0]["id"])
        self.assertIn("added by our team", registered[-1]["label"])
        with patch("hub.client_owner.owner_of", return_value=None), \
                patch("hub.client_owner.followers_of", return_value=[]), \
                patch("hub.client_owner.client_success_of", return_value={}):
            self.assertEqual(notices.attached("Nobody Co"), [])
            self.assertEqual(notices.optimized_all("Nobody Co", 4), 0)

    def test_staff_api_never_returns_other_clients_files(self):
        self.asset(); self.asset("private.png", self.other)
        response = self.http.get(f"/tools/image-picker/api/master-gallery?client_id={self.client.id}&name={self.other.name}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["filename"] for r in response.json["images"]], ["file.png"])

    def test_client_share_token_cannot_open_staff_catalog(self):
        response = self.app.test_client().get(f"/tools/image-picker/api/master-gallery?client_id={self.client.id}&token={self.client.share_token}")
        self.assertIn(response.status_code, (302, 401))

    def test_drive_campaign_is_creative_while_upload_is_upload(self):
        creative = catalog.organize({"collection_kind":"ad_asset", "provider":"google_drive", "io_number":"2072", "product_number":"p12214"})
        upload = catalog.organize({"collection_kind":"upload", "provider":"google_drive"})
        self.assertEqual(creative["section"], "creative")
        self.assertEqual(creative["folder"], "IO 2072 / Product p12214")
        self.assertEqual(upload["section"], "uploads")

    def test_legacy_reference_deduplicates_without_changing_asset(self):
        stored = self.asset()
        ref = catalog.reference("seo_images", "old", "Old image", stored.cloudinary_url,
                                public_id=stored.cloudinary_public_id)
        with patch.object(catalog, "legacy_images", return_value=[("SEO images", [ref], "")]):
            out = catalog.catalog(self.db, self.client, self.client.name)
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["images"][0]["public_id"], "legacy/file.png")
        self.assertEqual(out["images"][0]["id"], stored.id)

    def test_failed_source_is_partial_not_complete(self):
        with patch.object(catalog, "legacy_images", return_value=[("SEO images", [], "Unavailable")]):
            out = catalog.catalog(self.db, self.client, self.client.name)
        self.assertFalse(out["complete"])
        self.assertEqual(out["sources"][1]["error"], "Unavailable")

    def test_legacy_readers_use_exact_owner_and_skip_pending_blog_images(self):
        from hub import image_audit
        source = {"key":"blog_images", "label":"Blogs", "reader":lambda: [
            {"id":1,"client":"Example Co","label":"approved","url":"https://example.test/a.png","where":"approved"},
            {"id":2,"client":"Example Co","label":"pending","url":"https://example.test/b.png","where":"pending"},
            {"id":3,"client":"Example Co Supply","label":"other","url":"https://example.test/c.png","where":"approved"}]}
        # The original function is preserved below before setUp installs mocks.
        with patch.object(image_audit, "STORES", [source]):
            rows = list(legacy_reader(self.client.name))
        self.assertEqual([r["filename"] for r in rows[0][1]], ["approved"])

    def test_projects_match_exact_client_and_encode_ids(self):
        specs=[("Tool", lambda: [{"id":"one & two","client":"Example Co","title":"Project"},
                                 {"id":"other","client":"Example Co Supply","title":"Private"}],
                "client", "title", "/tool/?project=")]
        with patch.object(catalog, "project_sources", return_value=specs):
            out=catalog.catalog(self.db,self.client,self.client.name)
        self.assertEqual(len(out["projects"]),1)
        self.assertEqual(out["projects"][0]["url"],"/tool/?project=one%20%26%20two")
        self.assertFalse(out["projects"][0]["editable"])

    def test_all_assets_include_records_beyond_200(self):
        for i in range(225): self.asset(f"photo-{i}.png",collection_kind="upload")
        out=catalog.catalog(self.db,self.client,self.client.name)
        self.assertEqual(out["total"],225)
        self.assertIn("photo-0.png",[r["filename"] for r in out["images"]])
        with patch.object(picker,"resolve_scope",return_value=(self.db,self.client,False)):
            data=self.http.get("/tools/image-picker/api/saved?q=photo-0.png").json
        self.assertEqual(data["total"],1)
        self.assertEqual(data["images"][0]["filename"],"photo-0.png")

    def test_pagination_reports_remaining_records(self):
        for i in range(3): self.asset(f"{i}.png")
        with patch.object(picker,"resolve_scope",return_value=(self.db,self.client,False)):
            data=self.http.get("/tools/image-picker/api/saved?limit=2&offset=2").json
        self.assertEqual(len(data["images"]),1)
        self.assertEqual(data["total"],3)
        self.assertFalse(data["has_more"])

    def test_no_gallery_still_renders_master_home_without_write(self):
        with patch("modules.image_picker.provisioning.find",return_value=([],"")):
            response=self.http.get("/tools/image-picker/gallery/for-client?name=New%20Client")
        self.assertEqual(response.status_code,200)
        for label in (b"Client Uploads", b"Creative", b"Hub Projects", b"Set up uploads"):
            self.assertIn(label,response.data)
        self.assertEqual(len(self.db.scalars(select(PickerClient)).all()),2)

    def test_ambiguous_name_refuses_catalog(self):
        with patch("modules.image_picker.provisioning.find",return_value=([self.client,self.other],"")):
            response=self.http.get("/tools/image-picker/api/master-gallery?name=Example")
        self.assertEqual(response.status_code,409)

    def test_unsafe_references_are_not_clickable(self):
        for url in ("javascript:alert(1)","//evil.test/x","data:text/html,hello","https://[invalid"):
            self.assertEqual(catalog.safe_url(url),"")


class NativeProjectTests(unittest.TestCase):
    def test_commercials_keep_approval_and_client_ownership(self):
        from hub.extensions import db
        from modules.commercial_builder.models import Client, CommercialProject, RenderJob, RenderApproval
        app = Flask('commercial-catalog-test')
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
        db.init_app(app)
        with app.app_context():
            db.create_all()
            c = Client(name='Example Co', slug='example')
            other = Client(name='Example Co Supply', slug='supply')
            db.session.add_all([c, other]); db.session.flush()
            p = CommercialProject(client_id=c.id, title='Fall commercial')
            p.music = {'voice_track_url':'https://example.test/voice.mp3','voice_track_stale':True}
            private = CommercialProject(client_id=other.id, title='Private project')
            db.session.add_all([p, private]); db.session.flush()
            approved = RenderJob(project_id=p.id,status='succeeded',format='16:9',output_url='https://example.test/render.mp4')
            draft = RenderJob(project_id=p.id,status='succeeded',format='9:16',output_url='https://example.test/draft.mp4')
            failed = RenderJob(project_id=p.id,status='failed',output_url='https://example.test/failed.mp4')
            db.session.add_all([approved,draft,failed]); db.session.flush()
            db.session.add(RenderApproval(render_job_id=approved.id,project_id=p.id,stored_url='https://example.test/approved.mp4'))
            db.session.commit()
            rows = list(catalog.commercial_projects('Example Co'))
            self.assertEqual(len(rows),4)
            by_url = {r['url']:r for r in rows}
            self.assertEqual(by_url['https://example.test/approved.mp4']['status'],'Approved')
            self.assertEqual(by_url['https://example.test/draft.mp4']['status'],'Rendered — review in project')
            self.assertEqual(by_url['https://example.test/voice.mp3']['status'],'Needs regeneration')
            self.assertFalse(any('Private' in r['filename'] for r in rows))
            self.assertEqual(db.session.query(RenderApproval).count(),1)
            db.session.remove(); db.engine.dispose()

    def test_proposal_reads_saved_files_without_generating_or_backfilling(self):
        from modules.sales_builder import app as sales
        engine = create_engine('sqlite://')
        sales.Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add_all([
                sales.Quote(quote_number='1234',client='Example Co',pdf_url='https://example.test/proposal.pdf',io_client_pdf_url='https://example.test/io.pdf'),
                sales.Quote(quote_number='1235',client='Example Co Supply',pdf_url='https://example.test/private.pdf')])
            session.commit()
        with patch.object(sales,'SessionLocal',side_effect=lambda:Session(engine)), patch('hub.proposals.list_proposals',return_value=[]) as archive:
            rows = list(catalog.proposal_projects('Example Co'))
        self.assertEqual(len(rows),3)
        self.assertFalse(any('private.pdf' in r['url'] for r in rows))
        archive.assert_called_once_with('Example Co',backfill=False)
        engine.dispose()

    def test_radio_project_retains_voice_and_final_mix(self):
        row = {'id':'radio1','client':'Example Co','project_name':'Fall radio',
               'spots':[{'id':'voice1','audio_url':'https://example.test/voice.mp3'}],
               'mixes':{'30':{'audio_url':'https://example.test/final.mp3'}}}
        rows=list(catalog.project_files('Radio Promo',row))
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['media_type']=='audio' and r['folder']=='Fall radio' for r in rows))


legacy_reader = catalog.legacy_images
if __name__ == "__main__": unittest.main(verbosity=2)
