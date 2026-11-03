# Each popup state, entered right after "Listening" (catches formatting leaking between states)
osd "listening OnePlus Buds 4"; sleep 0.8; shot m1_listening
osd transcribing; sleep 0.6; shot m2_transcribing
osd "listening OnePlus Buds 4"; sleep 0.5; osd "nofield Hello"; sleep 0.6; shot m3_nofield
osd "listening OnePlus Buds 4"; sleep 0.5; osd "error Your microphone is muted|Unmute it from the sound menu in the top bar"; sleep 0.6; shot m4_error
# A state's colour mustn't outlive the popup: "Listening" after a hidden red "Didn't catch that" is green
osd empty; sleep 0.5; osd hide; sleep 0.8; osd "listening OnePlus Buds 4"; sleep 0.8; shot m9_listening_after_empty
