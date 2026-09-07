# Search quality audit - 2026-09-07

Ran **220 read-only provider searches across 110 distinct requests**, collecting 1752 candidate records through production Lavalink. No audio was played and no services were changed.

**Result:** 109 requests with a reviewed song/version expectation select an acceptable upload in the captured results. The remaining request, `hello`, is ambiguous: regular YouTube ranks OMFG above Adele and Lionel Richie. The test for that case preserves provider order; it does not claim to infer the intended artist.

## What testing changed

- Use regular YouTube for text searches. The music search endpoint omitted the expected recording or ranked unrelated covers first for several common queries.
- Remove bonuses for labels such as Audio, Lyrics, and Topic, and remove the exact-title bonus that penalized featured-artist credits. Preserve provider relevance against small metadata differences.
- Respect explicit artist names while accepting movie/album context around a complete song title.
- Match accents and Unicode decorations, recognize version aliases, and avoid unrequested covers, live performances, clips, and tempo changes.
- Interpret “not slowed”, “no remix”, and “without sped-up” as exclusions, including during playback recovery and in displayed titles.
- Use the music catalogue when regular YouTube is empty or leads with an unrequested multilingual compilation. Keep existing YouTube results if that optional catalogue lookup is unavailable.

## Validation and limits

- 286 offline tests pass, including 110 captured-result cases and focused source-routing, variant, recovery, and display regressions. Python compilation and diff whitespace checks pass.
- The first 70 requests informed the initial tuning; a separate batch of 40 caught accent, negation, and multilingual issues that were then fixed. These are reviewed regression examples, not an unbiased accuracy benchmark.
- Expectations concern song identity and the version stated in public metadata. No full audio listening, decoder test, or authenticity verification was performed. A mislabeled upload can still pass metadata checks.
- Provider results change. Broad same-title searches remain ambiguous without an artist. Music videos may include introductions or different edits from album audio.
- This audit validated the changes on `dev` before deployment; it did not change production services.

## Query-by-query results

An upload credited to a lyrics channel can still contain the intended recording. The links identify the reviewed upload, not an independently authenticated artist account.

| Request | Selected upload | Review |
| --- | --- | --- |
| Janice STFU | [Drake — Drake - Janice STFU](https://www.youtube.com/watch?v=SD4yRDY9mek) | Song/version matches metadata |
| janice stfu drake | [Drake — Drake - Janice STFU](https://www.youtube.com/watch?v=SD4yRDY9mek) | Song/version matches metadata |
| janice stfu chopped screwed | [Tha Audio Unit — Janice STFU (Chopped & Screwed)](https://www.youtube.com/watch?v=rLpYF69S9ik) | Song/version matches metadata |
| hotline bling | [Drake — Drake - Hotline Bling](https://www.youtube.com/watch?v=uxpDa-c-4Mc) | Song/version matches metadata |
| drake hotline bling | [Drake — Drake - Hotline Bling](https://www.youtube.com/watch?v=uxpDa-c-4Mc) | Song/version matches metadata |
| hotline bling drake | [Drake — Drake - Hotline Bling](https://www.youtube.com/watch?v=uxpDa-c-4Mc) | Song/version matches metadata |
| hotline bling lyrics | [Lyrics Of The Song — Drake - Hotline Bling (Lyrics)](https://www.youtube.com/watch?v=LxxnSDmGUc4) | Song/version matches metadata |
| hotline bling official audio | [Drake Media — Drake - Hotline Bling (Official Audio)](https://www.youtube.com/watch?v=zt6aRKpf9T4) | Song/version matches metadata |
| hotline bling clean | [Only Clean Edits — Drake - Hotline Bling (Super Clean)](https://www.youtube.com/watch?v=pDWDFH_Mg-U) | Song/version matches metadata |
| hotline bling instrumental | [ElPabloNadaMas xd — Drake - Hotline Bling (Instrumental)](https://www.youtube.com/watch?v=Y6VCUHqWPq0) | Song/version matches metadata |
| hotline bling remix | [TikTokTunes — Drake - Hotline Bling (Arabic Remix) x Sherine - Eh Eh \| you used to call me on my cell phone arabic](https://www.youtube.com/watch?v=IKna7tfWITI) | Song/version matches metadata |
| hotline bling slowed | [* r e n i x * — Drake- Hotline Bling ( slowed + reverb )](https://www.youtube.com/watch?v=D-EnOCfnszI) | Song/version matches metadata |
| hotline bling sped up | [julia — hotline bling - drake (sped up)](https://www.youtube.com/watch?v=_rYIQtl1QN4) | Song/version matches metadata |
| hotline bling cover | [Matúš Komarňanský — Drake - Hotline Bling (Soku Cover)](https://www.youtube.com/watch?v=YHs9ZUt6NgA) | Song/version matches metadata |
| hotline bling billie eilish | [BillieEilishVEVO — Billie Eilish - hotline bling (Audio)](https://www.youtube.com/watch?v=q-jeSuFWGDM) | Song/version matches metadata |
| hotline blng | [Drake — Drake - Hotline Bling](https://www.youtube.com/watch?v=uxpDa-c-4Mc) | Song/version matches metadata |
| hot line bling | [Drake — Drake - Hotline Bling](https://www.youtube.com/watch?v=uxpDa-c-4Mc) | Song/version matches metadata |
| kpop demon golden | [Sony Pictures Animation — “Golden” Official Lyric Video \| KPop Demon Hunters \| Sony Animation](https://www.youtube.com/watch?v=yebNIHKAC4A) | Song/version matches metadata |
| golden kpop demon hunters | [Sony Pictures Animation — “Golden” Official Lyric Video \| KPop Demon Hunters \| Sony Animation](https://www.youtube.com/watch?v=yebNIHKAC4A) | Song/version matches metadata |
| golden huntrix | [Sony Pictures Animation — “Golden” Official Lyric Video \| KPop Demon Hunters \| Sony Animation](https://www.youtube.com/watch?v=yebNIHKAC4A) | Song/version matches metadata |
| golden h untr x | [Zethy's Musics — Golden - HUNTR/X (HQ Audio)](https://www.youtube.com/watch?v=rJbSDZSt0cs) | Song/version matches metadata |
| golden kpop | [Sony Pictures Animation — “Golden” Official Lyric Video \| KPop Demon Hunters \| Sony Animation](https://www.youtube.com/watch?v=yebNIHKAC4A) | Song/version matches metadata |
| golden | [Sony Pictures Animation — “Golden” Official Lyric Video \| KPop Demon Hunters \| Sony Animation](https://www.youtube.com/watch?v=yebNIHKAC4A) | Song/version matches metadata |
| golden harry styles | [Harry Styles — Harry Styles - Golden (Official Video)](https://www.youtube.com/watch?v=P3cffdsEXXw) | Song/version matches metadata |
| golden kpop demon hunters lyrics | [Sony Pictures Animation — “Golden” Official Lyric Video \| KPop Demon Hunters \| Sony Animation](https://www.youtube.com/watch?v=yebNIHKAC4A) | Song/version matches metadata |
| golden kpop demon hunters instrumental | [HUNTR/X - Topic — Golden (Instrumental)](https://www.youtube.com/watch?v=Wbt9E3YCiWo) | Song/version matches metadata |
| golden kpop demon hunters karaoke | [Musisi Karaoke — Golden - Kpop Demon Hunters (Karaoke Songs With Lyrics - Original Key)](https://www.youtube.com/watch?v=fQ7Bt10jUfM) | Song/version matches metadata |
| golden kpop demon hunters sped up | [الله أكبر، الحمد لله  — Kpop demon hunters golden ( sped up )](https://www.youtube.com/watch?v=aVuAvVPu4FY) | Song/version matches metadata |
| golden kpop demon hunters cover | [The Kelly Clarkson Show — 'Golden' from Kpop Demon Hunters \| Kelly Clarkson Kellyoke Cover](https://www.youtube.com/watch?v=py_P2TI-gNg) | Song/version matches metadata |
| golden kpop demon hunters live | [RemasterKingdom7.0 — HUNTR/X - KPop Demon Hunters \| Golden \| Full Performance \| Live @ The Oscars Awards 2026](https://www.youtube.com/watch?v=kes94PVdEBw) | Song/version matches metadata |
| soda pop kpop demon hunters | [Sony Pictures Animation — "Soda Pop" Official Lyric Video \| KPop Demon Hunters \| Sony Animation](https://www.youtube.com/watch?v=983bBbJx0Mk) | Song/version matches metadata |
| your idol saja boys | [Netflix — ‘Your Idol’ Lyric Video \| KPop Demon Hunters \| Netflix](https://www.youtube.com/watch?v=fGyTN5UjnL4) | Song/version matches metadata |
| blinding lights | [The Weeknd — The Weeknd - Blinding Lights (Official Video)](https://www.youtube.com/watch?v=4NRXx6U8ABQ) | Song/version matches metadata |
| weeknd blinding lights | [The Weeknd — The Weeknd - Blinding Lights (Official Video)](https://www.youtube.com/watch?v=4NRXx6U8ABQ) | Song/version matches metadata |
| blinding lights slowed reverb | [PLAYYYPRETEND — the weeknd - blinding lights (slowed + reverb)](https://www.youtube.com/watch?v=iDVKMdnvgl8) | Song/version matches metadata |
| one more time daft punk | [Daft Punk — Daft Punk - One More Time (Official Video)](https://www.youtube.com/watch?v=FGBhQbmPwH8) | Song/version matches metadata |
| daft punk one more time extended | [Endless Stream — Daft Punk — One More Time (Extended)](https://www.youtube.com/watch?v=4oOdcZlmE0Y) | Song/version matches metadata |
| never gonna give you up | [Rick Astley — Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)](https://www.youtube.com/watch?v=dQw4w9WgXcQ) | Song/version matches metadata |
| rick astley never gona give u up | [Rick Astley — Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)](https://www.youtube.com/watch?v=dQw4w9WgXcQ) | Song/version matches metadata |
| bohemian rhapsody | [Queen Official — Queen – Bohemian Rhapsody (Official Video Remastered)](https://www.youtube.com/watch?v=fJ9rUzIMcZQ) | Song/version matches metadata |
| queen bohemian rhapsody live aid | [Live Aid and Queen Official — Queen - Bohemian Rhapsody (Live Aid 1985)](https://www.youtube.com/watch?v=vbvyNnw8Qjg) | Song/version matches metadata |
| numb linkin park | [Linkin Park — Numb (Official Music Video) \[4K UPGRADE\] – Linkin Park](https://www.youtube.com/watch?v=kXYiU_JCYtU) | Song/version matches metadata |
| numb encore | [Linkin Park — Numb / Encore (Official Audio) - Linkin Park / JAY-Z](https://www.youtube.com/watch?v=_1oO4wzPCmE) | Song/version matches metadata |
| in the end | [Linkin Park — In The End \[Official HD Music Video\] - Linkin Park](https://www.youtube.com/watch?v=eVTXPUF4Oz4) | Song/version matches metadata |
| taylor swift love story | [Taylor Swift — Taylor Swift - Love Story](https://www.youtube.com/watch?v=8xg3vE8Ie_E) | Song/version matches metadata |
| love story taylors version | [Taylor Swift — Taylor Swift - Love Story (Taylor’s Version) \[Official Lyric Video\]](https://www.youtube.com/watch?v=aXzVF3XeS8M) | Song/version matches metadata |
| taylor swift anti hero | [Taylor Swift — Taylor Swift - Anti-Hero (Official Music Video)](https://www.youtube.com/watch?v=b1kbLwvqugk) | Song/version matches metadata |
| anti-hero | [Taylor Swift — Taylor Swift - Anti-Hero (Official Music Video)](https://www.youtube.com/watch?v=b1kbLwvqugk) | Song/version matches metadata |
| take on me | [a-ha — a-ha - Take On Me (Official Video) \[4K\]](https://www.youtube.com/watch?v=djV11Xbc914) | Song/version matches metadata |
| take on me acoustic | [Mais 1 Clipe Com Legenda — A-ha - Take on Me (MTV Unplugged - Summer Solstice) \[Tradução\]](https://www.youtube.com/watch?v=hyPudrPNSuQ) | Song/version matches metadata |
| somebody that i used to know | [Gotye — Gotye - Somebody That I Used To Know (feat. Kimbra) \[Official Music Video\]](https://www.youtube.com/watch?v=8UVNT4wvIGY) | Song/version matches metadata |
| gotye somebody i used to know | [Gotye — Gotye - Somebody That I Used To Know (feat. Kimbra) \[Official Music Video\]](https://www.youtube.com/watch?v=8UVNT4wvIGY) | Song/version matches metadata |
| despacito | [Luis Fonsi — Luis Fonsi - Despacito ft. Daddy Yankee](https://www.youtube.com/watch?v=kJQP7kiw5Fk) | Song/version matches metadata |
| despacito justin bieber remix | [Unique Sound — Luis Fonsi, Daddy Yankee - Despacito (Remix) \[Lyrics\] Ft. Justin Bieber](https://www.youtube.com/watch?v=37kFBTbrjfg) | Song/version matches metadata |
| bad bunny dákiti | [Bad Bunny — BAD BUNNY x JHAY CORTEZ - DÁKITI \| EL ÚLTIMO TOUR DEL MUNDO (Official Video)](https://www.youtube.com/watch?v=TmKh7lAwnBI) | Song/version matches metadata |
| bts dynamite | [HYBE LABELS — BTS (방탄소년단) 'Dynamite' Official MV](https://www.youtube.com/watch?v=gdZLi9oWNZg) | Song/version matches metadata |
| blackpink how you like that | [BLACKPINK — BLACKPINK - 'How You Like That' DANCE PERFORMANCE VIDEO](https://www.youtube.com/watch?v=32si5cfrCNc) | Song/version matches metadata |
| gangnam style | [officialpsy — PSY - GANGNAM STYLE(강남스타일) M/V](https://www.youtube.com/watch?v=9bZkp7q19f0) | Song/version matches metadata |
| APT rose bruno mars | [ROSÉ and Bruno Mars — ROSÉ & Bruno Mars - APT. (Official Music Video)](https://www.youtube.com/watch?v=ekr2nIex040) | Song/version matches metadata |
| chappell roan good luck babe | [Chappell Roan — Chappell Roan - Good Luck, Babe! (Official Lyric Video)](https://www.youtube.com/watch?v=1RKqOmSkGgM) | Song/version matches metadata |
| billie eilish birds of a feather | [Billie Eilish — Billie Eilish - BIRDS OF A FEATHER (Official Music Video)](https://www.youtube.com/watch?v=V9PVRfjEBTI) | Song/version matches metadata |
| sabrina espresso | [Sabrina Carpenter — Sabrina Carpenter - Espresso](https://www.youtube.com/watch?v=eVli-tstM5E) | Song/version matches metadata |
| metallica nothing else matters | [Metallica — Metallica: Nothing Else Matters (Official Music Video)](https://www.youtube.com/watch?v=tAGnKpE4NCI) | Song/version matches metadata |
| nirvana smells like teen spirit | [Nirvana — Nirvana - Smells Like Teen Spirit (Official Music Video)](https://www.youtube.com/watch?v=hTWKbfoikeg) | Song/version matches metadata |
| avicii levels | [Avicii — Avicii - Levels](https://www.youtube.com/watch?v=_ovdm2yX4MA) | Song/version matches metadata |
| darude sandstorm | [Darude — Darude - Sandstorm](https://www.youtube.com/watch?v=y6120QOlsfU) | Song/version matches metadata |
| rockefeller street nightcore | [RauchHuhn — Nightcore Classics - Rockefeller Street \[HD\]](https://www.youtube.com/watch?v=7UuHyBsUSB8) | Song/version matches metadata |
| live lightning crashes | [LIVE — Live - Lightning Crashes](https://www.youtube.com/watch?v=xsJ4O-nSveg) | Song/version matches metadata |
| clean taylor swift | [Taylor Swift — Taylor Swift - Clean (Taylor's Version) (Lyric Video)](https://www.youtube.com/watch?v=AppsjTInqiw) | Song/version matches metadata |
| live and let die | [PAUL McCARTNEY — Live And Let Die](https://www.youtube.com/watch?v=08saJgLmytU) | Song/version matches metadata |
| hello adele | [Adele — Adele - Hello (Official Music Video)](https://www.youtube.com/watch?v=YQHsXMglC9A) | Song/version matches metadata |
| hello lionel richie | [lionelrichie — Lionel Richie - Hello (Official Music Video)](https://www.youtube.com/watch?v=mHONNcZbwDY) | Song/version matches metadata |
| hello | [Robinou — OMFG - Hello (Official Audio)](https://www.youtube.com/watch?v=hxHOKq0OdLU) | Ambiguous artist |
| zombie cranberries | [TheCranberriesTV — The Cranberries - Zombie (Official Music Video)](https://www.youtube.com/watch?v=6Ejga4kJUts) | Song/version matches metadata |
| zombie bad wolves | [Better Noise Music — Bad Wolves - Zombie (Official Video)](https://www.youtube.com/watch?v=9XaS93WMRQQ) | Song/version matches metadata |
| creep radiohead | [Radiohead — Radiohead - Creep](https://www.youtube.com/watch?v=XFkzRNyygfk) | Song/version matches metadata |
| creep acoustic radiohead | [FunInFuneral13 — Creep (Acoustic)-Radiohead (Studio Version)](https://www.youtube.com/watch?v=rMbNcdoFDRs) | Song/version matches metadata |
| hotel california | [Eagles — Eagles - Hotel California (Official Audio)](https://www.youtube.com/watch?v=dLl4PZtxia8) | Song/version matches metadata |
| hotel california live | [Eagles — Eagles - Hotel California (Live 1977) (Official Video) \[HD\]](https://www.youtube.com/watch?v=09839DpTctU) | Song/version matches metadata |
| sweet dreams eurythmics | [Eurythmics — Eurythmics, Annie Lennox, Dave Stewart - Sweet Dreams (Are Made Of This) (Official Video)](https://www.youtube.com/watch?v=qeMFqkcPYcg) | Song/version matches metadata |
| sweet dreams marilyn manson | [Marilyn Manson — Marilyn Manson - Sweet Dreams (Are Made Of This) (Alt. Version)](https://www.youtube.com/watch?v=QUvVdTlA23w) | Song/version matches metadata |
| sweet child o mine | [Guns N' Roses — Guns N' Roses - Sweet Child O' Mine (Official Music Video)](https://www.youtube.com/watch?v=1w7OgIMMRc4) | Song/version matches metadata |
| guns roses sweet child of mine | [Guns N' Roses — Guns N' Roses - Sweet Child O' Mine (Official Music Video)](https://www.youtube.com/watch?v=1w7OgIMMRc4) | Song/version matches metadata |
| lose yourself eminem | [EminemMusic — Eminem - Lose Yourself](https://www.youtube.com/watch?v=xFYQQPAOz7Y) | Song/version matches metadata |
| eminem lose yourself clean | [Throwback Hits — Eminem - Lose Yourself (Clean) \| Lyrics](https://www.youtube.com/watch?v=kY5gClJdbH8) | Song/version matches metadata |
| eminem lose yourself instrumental | [EminemMusic — Lose Yourself (Instrumental)](https://www.youtube.com/watch?v=BETUM7WsWjE) | Song/version matches metadata |
| bring me to life | [RockHype — Evanescence - Bring Me To Life](https://www.youtube.com/watch?v=ltoaQo2ynSo) | Song/version matches metadata |
| evanescence bring me to life | [Evanescence — Evanescence - Bring Me To Life (Official HD Music Video) ft. Paul McCoy](https://www.youtube.com/watch?v=3YxaaGgTQYM) | Song/version matches metadata |
| linkin park in the end piano cover | [Jova Musique - Pianella Piano — Linkin Park - In The End \| Piano Cover by Pianella Piano](https://www.youtube.com/watch?v=h9E4URyBvCI) | Song/version matches metadata |
| toto africa | [TOTO — Toto - Africa (Official HD Video)](https://www.youtube.com/watch?v=FTQbiNvZqaY) | Song/version matches metadata |
| africa weezer | [weezer — Weezer - Africa (starring Weird Al Yankovic)](https://www.youtube.com/watch?v=mk5Dwg5zm2U) | Song/version matches metadata |
| stay kid laroi justin bieber | [The Kid LAROI. — The Kid LAROI, Justin Bieber - STAY (Official Video)](https://www.youtube.com/watch?v=kTJczUoc26U) | Song/version matches metadata |
| stay rihanna | [Unique Sound — Rihanna - Stay (Lyrics) ft. Mikky Ekko](https://www.youtube.com/watch?v=_bXqoIzH0N8) | Song/version matches metadata |
| du hast | [Rammstein Official — Rammstein - Du Hast (Official 4K Video)](https://www.youtube.com/watch?v=W3q8Od5qJio) | Song/version matches metadata |
| rammstein du hast | [Rammstein Official — Rammstein - Du Hast (Official 4K Video)](https://www.youtube.com/watch?v=W3q8Od5qJio) | Song/version matches metadata |
| måneskin beggin | [LatinHype — Måneskin - Beggin' (Lyrics/Testo)](https://www.youtube.com/watch?v=W2MpGCL8-9o) | Song/version matches metadata |
| man eskin beggin | [LatinHype — Måneskin - Beggin' (Lyrics/Testo)](https://www.youtube.com/watch?v=W2MpGCL8-9o) | Song/version matches metadata |
| rosé apt | [ROSÉ and Bruno Mars — ROSÉ & Bruno Mars - APT. (Official Music Video)](https://www.youtube.com/watch?v=ekr2nIex040) | Song/version matches metadata |
| rose apt | [ROSÉ and Bruno Mars — ROSÉ & Bruno Mars - APT. (Official Music Video)](https://www.youtube.com/watch?v=ekr2nIex040) | Song/version matches metadata |
| one more time | [Daft Punk — Daft Punk - One More Time (Official Video)](https://www.youtube.com/watch?v=FGBhQbmPwH8) | Song/version matches metadata |
| golden david guetta remix | [Still Watching Netflix — KPop Demon Hunters \| "Golden" - The David Guetta Remix \| Netflix](https://www.youtube.com/watch?v=mI5-Bi2Wc5c) | Song/version matches metadata |
| golden french version | [Shihiro — HUNTRIX 'Golden' - 'Briller' Lyrics (French Version) \[Complete Version\]](https://www.youtube.com/watch?v=cvE8DM34GuU) | Song/version matches metadata |
| kpop demon hunters golden original | [Sony Pictures Animation — “Golden” Official Lyric Video \| KPop Demon Hunters \| Sony Animation](https://www.youtube.com/watch?v=yebNIHKAC4A) | Song/version matches metadata |
| drake hotline bling not slowed | [Drake — Drake - Hotline Bling](https://www.youtube.com/watch?v=uxpDa-c-4Mc) | Song/version matches metadata |
| drake hotline bling no remix | [Drake — Drake - Hotline Bling](https://www.youtube.com/watch?v=uxpDa-c-4Mc) | Song/version matches metadata |
| never gonna give you up 1 hour | [Daily Dose Of Songs — Never Gonna Give You Up - 1 Hour Version - Rick Astley (Lyrics)](https://www.youtube.com/watch?v=la-GFyRzIRA) | Song/version matches metadata |
| bohemian rhapsody lyrics | [ecsl — Queen - Bohemian Rhapsody (with lyrics)](https://www.youtube.com/watch?v=axAtWjn3MfI) | Song/version matches metadata |
| numb acoustic linkin park | [L O O P B A C K — Linkin Park - Numb ( Acoustic Version )](https://www.youtube.com/watch?v=0IM0G3YmPcA) | Song/version matches metadata |
| skyfall adele | [Adele — Adele - Skyfall (Official Lyric Video)](https://www.youtube.com/watch?v=DeumyOzKqgI) | Song/version matches metadata |
| let it go frozen | [Idina Menzel — Let It Go (From "Frozen"/Soundtrack Version)](https://www.youtube.com/watch?v=qSU560anReg) | Song/version matches metadata |
