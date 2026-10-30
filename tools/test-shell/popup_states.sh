# Each popup state, entered right after "Listening" (catches formatting leaking between states)
osd "listening OnePlus Buds 4"; sleep 0.8; shot m1_listening
osd transcribing; sleep 0.6; shot m2_transcribing
osd "listening OnePlus Buds 4"; sleep 0.5; osd "nofield Hello"; sleep 0.6; shot m3_nofield
osd "listening OnePlus Buds 4"; sleep 0.5; osd "error Your microphone is muted|Unmute it from the sound menu in the top bar"; sleep 0.6; shot m4_error
