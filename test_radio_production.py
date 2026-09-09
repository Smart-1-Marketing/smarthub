"""Radio Ad Creator production flow, using real stores/routes and a stub HTTP provider."""
import base64
import io
import os
import tempfile
import unittest
from unittest.mock import patch, Mock
from modules.radio_promo import app as promo, store, voices, music_library
from modules.fan_radio import app as fan, store as fan_store, voices as fan_voices
from hub import customer_voices as cv, jsonstore

class RadioProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,{'HUB_DATA_DIR':self.tmp.name,'DATABASE_URL':''});env.start();self.addCleanup(env.stop)
        self.addCleanup(lambda: jsonstore._engine.dispose() if jsonstore._engine is not None else None)
        self.client=promo.app.test_client()
        self.project=store.create(dict(company='Example',home_url='https://example.test',slots=['fifteen','thirty']))
        self.pid=self.project['id'];self.base='/api/projects/'+self.pid
        self.text='Example has something special for you. Visit example.test today.'
        scripts={k:promo._decorate(k,self.text,[]) for k in ('fifteen','thirty')}
        store.update(self.pid,{'scripts':scripts})
        jsonstore.write_json(cv._path(),{'test':dict(id='test',voice_id='customer123',name='Customer voice',status='ready')})
        self.chosen=dict(voice_id='customer123',name='Customer voice',model_id='eleven_v3',speed=1.1,stability=.5,prompt_strength=.8)
        result=Mock(status_code=200,json=lambda:dict(audio_base64=base64.b64encode(b'audio').decode(),alignment={'characters':['a'],'character_start_times_seconds':[0],'character_end_times_seconds':[14]}))
        self.http=patch.object(voices.requests,'post',return_value=result).start();self.addCleanup(patch.stopall)
        patch.object(voices,'_headers',return_value={}).start();patch.object(fan_voices,'_headers',return_value={}).start()
        patch.object(promo,'cloud_ready',return_value=False).start()
        patch.object(voices,'_note_characters').start();patch.object(fan_voices,'_note_characters').start()

    def test_customer_voice_saved_preview_and_record_use_identical_controls(self):
        saved=self.client.post(self.base+'/voice',json=self.chosen)
        self.assertEqual(saved.status_code,200,saved.json)
        self.assertEqual(self.client.get(self.base).json['project']['voice']['voice_id'],'customer123')
        preview=self.client.post(self.base+'/voice/preview',json={'text':'[excited] Hello there!'})
        self.assertEqual(preview.status_code,200,preview.json)
        sample_payload=self.http.call_args.kwargs['json'];self.assertIn('/customer123/',self.http.call_args.args[0])
        self.assertEqual(sample_payload['voice_settings']['speed'],1.1)
        with patch.object(promo.qc,'blocking',return_value=[]):
            recorded=self.client.post(self.base+'/render',json={'slot':'fifteen'})
        self.assertEqual(recorded.status_code,200,recorded.json)
        self.assertEqual(self.http.call_args.kwargs['json']['voice_settings'],sample_payload['voice_settings'])
        self.assertEqual(self.http.call_args.kwargs['json']['model_id'],'eleven_v3')
        self.assertEqual(recorded.json['project']['spots'][0]['voice_id'],'customer123')
        self.assertEqual(recorded.json['project']['spots'][0]['voice_name'],'Customer voice')

    def test_pending_customer_refused_before_paid_request_in_both_tools(self):
        cv._save('test',{'status':'verification_required'})
        self.assertEqual(self.client.post(self.base+'/voice',json=self.chosen).status_code,400)
        for renderer in (voices,fan_voices):
            with self.assertRaises(renderer.VoiceError):renderer.render_audio('customer123','Hello')
        self.http.assert_not_called()

    def test_fan_customer_preview_uses_same_custom_id_and_settings(self):
        p=fan_store.create(dict(company='Example'),'Tester');p['voice']=self.chosen;fan_store.save(p)
        r=fan.app.test_client().post('/api/projects/'+p['id']+'/voice/preview',json={'text':'[excited] Hello there!'})
        self.assertEqual(r.status_code,200,r.json)
        self.assertIn('/customer123/',self.http.call_args.args[0]);payload=self.http.call_args.kwargs['json']
        self.assertEqual(payload['model_id'],'eleven_v3');self.assertEqual(payload['voice_settings']['style'],.8)
        self.assertEqual(payload['voice_settings']['stability'],.5);self.assertEqual(payload['voice_settings']['speed'],1.1)

    def test_invalid_controls_and_standard_tags_do_not_call_provider(self):
        for changes in ({'speed':2},{'stability':float('nan')},{'model_id':'unknown'}):
            self.assertEqual(self.client.post(self.base+'/voice',json={**self.chosen,**changes}).status_code,400)
        self.assertEqual(self.client.post(self.base+'/voice/preview',json={**self.chosen,'model_id':'eleven_multilingual_v2','text':'[excited] Hello'}).status_code,400)
        self.http.assert_not_called()

    def test_library_applies_only_to_eligible_or_selected_spots_and_retires_mix(self):
        def save(n,sec): return music_library.save(n,b'bed',dict(kind='composed',seconds=sec,measured=True),promo.upload_asset)
        short=save('Short bed',15);long=save('Long bed',30)
        store.update(self.pid,{'mixes':{'fifteen':{'audio_url':'old'},'thirty':{'audio_url':'old'}}})
        response=self.client.post(self.base+'/music-library/'+short['id']+'/apply',json={})
        self.assertEqual(response.json['applied'],['fifteen']);self.assertIn('thirty',response.json['project']['mixes'])
        self.assertEqual(self.client.post(self.base+'/music-library/'+short['id']+'/apply',json={'slot':'thirty'}).status_code,400)
        r=self.client.post(self.base+'/music-library/'+long['id']+'/apply',json={'slot':'thirty'})
        self.assertEqual(r.json['project']['beds']['fifteen']['name'],'Short bed');self.assertEqual(r.json['project']['beds']['thirty']['name'],'Long bed')
        self.assertEqual(r.json['project']['mixes'],{})
        self.assertEqual(self.client.get('/api/music-library?q=Long').json['tracks'][0]['id'],long['id'])
        with self.client.get(self.base+'/audio?ref=bed:thirty') as response:
            self.assertEqual(response.data,b'bed')

    def test_both_customer_tracks_and_comment_archive(self):
        row=store.update(self.pid,{'spots':[dict(slot='fifteen',audio_url='/voice.mp3',measured_seconds=14)],'beds':{'fifteen':{'kind':'composed'}},'mixes':{'fifteen':dict(audio_url='/mix.wav',seconds=15)},'feedback':[dict(spot_id='fifteen',comment='Old')],'decisions':{'fifteen':{'status':'approved'}}})
        unit=promo.public_view(row)['spots'][0];self.assertEqual(unit['voice_audio_url'],'/voice.mp3');self.assertEqual(unit['audio_url'],'/mix.wav');self.assertTrue(unit['has_bed'])
        clear=self.client.post(self.base+'/comments/clear',json={}).json['project']
        self.assertEqual(clear['feedback'],[]);self.assertEqual(clear['decisions']['fifteen']['status'],'approved')
        self.assertEqual(clear['feedback_archive'][0]['feedback'][0]['comment'],'Old');self.assertNotIn('feedback_archive',promo.public_view(clear))

    def test_replacing_reused_bed_does_not_overwrite_other_spot_audio(self):
        with patch.object(promo, '_measured', return_value={'seconds':30,'measured':True}):
            first=self.client.post(self.base+'/bed/upload',data={'slot':'thirty','file':(io.BytesIO(b'first'),'bed.mp3')})
            self.assertEqual(first.status_code,200,first.json)
            original=first.json['bed']['audio_url']
            self.assertEqual(self.client.post(self.base+'/bed/reuse',json={'source':'thirty'}).status_code,200)
            second=self.client.post(self.base+'/bed/upload',data={'slot':'thirty','file':(io.BytesIO(b'second'),'bed.mp3')})
            self.assertEqual(second.status_code,200,second.json)
            self.assertNotEqual(second.json['bed']['audio_url'],original)
            with self.client.get(self.base+'/audio?ref=bed:fifteen') as response:self.assertEqual(response.data,b'first')

    def test_script_edits_retire_old_recordings_and_approvals(self):
        store.update(self.pid,{'spots':[{'slot':'fifteen','audio_url':'old'}],'mixes':{'fifteen':{'audio_url':'oldmix'}},'decisions':{'fifteen':{'status':'approved'}}})
        with patch.object(promo,'_required_script_gaps',return_value=[]):
            r=self.client.post(self.base+'/script/edit',json={'slot':'fifteen','script':self.text+' New offer.'})
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['project']['spots'],[]);self.assertEqual(r.json['project']['mixes'],{});self.assertEqual(r.json['project']['decisions'],{})

    def test_mix_volume_persists_and_rejects_unknown_level(self):
        levels=promo.radio_spec.bed_levels()['levels']
        self.assertTrue(levels)
        r=self.client.post(self.base+'/mix-settings',json={'level':levels[0]['label']})
        self.assertEqual(r.json['project']['mix_level'],levels[0]['label'])
        self.assertEqual(self.client.post(self.base+'/mix-settings',json={'level':'invented'}).status_code,400)

    def test_preview_provider_error_recovers_on_retry(self):
        self.http.return_value.status_code=403
        bad=self.client.post(self.base+'/voice/preview',json={**self.chosen,'text':'Hello'})
        self.assertEqual(bad.status_code,400)
        self.http.return_value.status_code=200
        self.assertEqual(self.client.post(self.base+'/voice/preview',json={**self.chosen,'text':'Hello'}).status_code,200)
        self.assertNotIn('voice',store.get(self.pid))

if __name__=='__main__':unittest.main()
