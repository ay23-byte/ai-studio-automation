"""Create comparable female documentary narration audition tracks."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from core.voice import synthesise

TEXT = """26 अप्रैल 1986 की रात, चेरनोबिल न्यूक्लियर पावर प्लांट में एक ऐसा हादसा हुआ जिसने पूरी दुनिया को हिला दिया। लेकिन यह कहानी सिर्फ़ एक विस्फोट की नहीं है। यह कहानी है उन फैसलों की, जिनकी कीमत हज़ारों लोगों ने चुकाई। रात के समय, Reactor Number Four पर एक safety test चल रहा था। कुछ ही मिनटों में हालात नियंत्रण से बाहर हो गए। शहर के लोगों को शुरुआत में बताया गया कि सब कुछ सामान्य है। लेकिन हवा में फैल चुका radioactive material सीमाओं को पार कर रहा था। सवाल यह है: एक आधुनिक nuclear power plant में ऐसा कैसे हुआ? और जब अधिकारियों को सच्चाई पता चली, तब उन्होंने दुनिया को बताने में इतनी देर क्यों की? इस documentary में हम समझेंगे कि reactor के अंदर क्या हुआ, Pripyat को कैसे खाली कराया गया, और आज भी Chernobyl हमें nuclear safety के बारे में क्या सिखाता है।"""

VOICES = [
    ("swara_hindi", "hi-IN-SwaraNeural", "Hindi female"),
    ("neerja_indian_english", "en-IN-NeerjaNeural", "Indian English female"),
    ("sonia_british_english", "en-GB-SoniaNeural", "British English female"),
]


def main():
    root = Path("voice_audition")
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for label, voice, description in VOICES:
        folder = root / label
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        os.environ["TTS_ENGINE"] = "edge"
        os.environ["TTS_VOICE"] = voice
        os.environ["TTS_RATE"] = "-5%"
        os.environ["TTS_PITCH"] = "+0Hz"
        audio, _ = synthesise(TEXT, folder, language="hinglish", gender="female")
        report = json.loads((folder / "narration_synthesis_report.json").read_text(encoding="utf-8"))
        rows.append({
            "id": label, "description": description, "voice": voice,
            "audio": str(audio), "duration_seconds": report["assembled_duration_seconds"],
            "report": str(folder / "narration_synthesis_report.json"),
        })
    (root / "audition_index.json").write_text(json.dumps({
        "topic": "Chernobyl documentary narration comparison",
        "script_language": "Hinglish", "script": TEXT, "candidates": rows,
        "human_selection_required": True,
        "notice": "Choose by listening. No candidate is certified professional without human approval.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
