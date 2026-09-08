# Customer Voices

Open **Creative → Audio → Customer Voices**. Staff can upload a customer's recordings or save an existing voice from the connected ElevenLabs account. Enter the customer and speaker name and confirm that the speaker has authorized cloning and use.

Use 1–5 clear single-speaker MP3, WAV, M4A, AAC, OGG, or WebM recordings, ideally 1–3 minutes total. Each file may be up to 25 MB, with 50 MB total. Samples are relayed to ElevenLabs and are not retained in the Hub's voice library. The library persists customer, voice ID, name, permission confirmation, submitter, and verification status through the shared JSON store and its database mirror.

If ElevenLabs requires verification, complete it in that account and press **Refresh voice status**. Only ready voices appear in the shared picker. Existing projects also refuse synthesis with a managed voice that is not ready.

## Submitted client recordings

The Commercial Builder recording-link panel includes **Create reusable customer voice** for submitted recordings. That opens this library with the recording selected for review and cloning, so it does not need to be uploaded again. These source recordings remain stored by the separate voice-capture feature; the reusable voice library only records their capture ID and voice metadata. Revoked captures and recordings without speaker consent cannot be imported.

## Choose the voice

- **Radio Ad Creator:** choose a customer voice in Cast, then select the spot lengths it should read.
- **Fan Radio:** choose and use a customer voice to save it on the project, then record the spots.
- **Commercial Builder / Voice & music:** selecting a customer voice saves the client's narration voice, used by scene, batch, and full narration generation.
- **Commercial Builder / Blueprint / Presenter:** choose a customer voice alongside the avatar. ElevenLabs creates the speech; HeyGen receives the saved audio track. This requires ElevenLabs, media storage, and HeyGen connections.

The picker includes all staff-saved voices, labeled by customer, with the current customer's voices listed first where a customer name is available. Refresh the picker after saving a voice in the library.

## Interrupted requests

Clone submissions are reserved durably before calling ElevenLabs. Repeating an identical upload reuses its reservation or confirmed voice. If the outcome is uncertain, check ElevenLabs and save the existing voice ID if the clone completed. The Hub does not automatically repeat the paid clone request.

Presenter retries reuse an uploaded customer voice track for unchanged spoken text and voice. A failure before presenter submission preserves any previous clip and requires explicitly requesting a new take to retry uncertain speech generation. A HeyGen timeout retains the existing job reservation for status recovery.

## Validation

`test_customer_voices.py` covers authentication, upload validation, durable shared records, verification enforcement in each speech service, repeated uploads, uncertain provider responses, importing existing voices, and HeyGen audio payloads. `test_commercial_reliability.py` covers presenter audio reuse and preservation of existing clips after speech failure. The normal CI gate runs both.

Provider reference: https://elevenlabs.io/docs/api-reference/voices/ivc/create
