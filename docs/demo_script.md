# AstroGuard – Demo Script (3–5 minutes)

## Before presenting (2 minutes)

Use a fresh demo database, so that earlier rehearsal entries don't change
anything. Nothing is deleted:

```powershell
$env:ASTROGUARD_DB_PATH = "$PWD\demo_presentation.db"
python -m streamlit run app.py
```

- If you want to show real Actiwatch data, the Zenodo archive must be at
  `data\external\zenodo_4961281\` (see README). Import it once before the talk:
  **🌙 Sleep → ⌚ Actigraphy (recorded) → Import 2 participants…**. A
  **⌚ Real Actiwatch research data** button then appears under the scenario buttons.
- Optional: turn on **🔊 Alert sound**. Browsers may block autoplay; the
  dashboard then shows a *Play alert tone* button.
- Switching profiles returns you to the Overview tab. Reopen the tab you need.

## Script

| # | Time | Do | Say |
|---|---|---|---|
| 1 | 0:00 | Show the AstroGuard header. | "Astronauts often sleep less and on shifting schedules, and that can slow reactions. AstroGuard is decision support that compares each person with **their own** baseline and explains what it sees. It's a research prototype, not a medical device." |
| 2 | 0:25 | Click **⌚ Real Actiwatch research data**, then open **🌙 Sleep → ⌚ Actigraphy (recorded)**. Point to the green REAL WEARABLE DATA card and its Zenodo link. | "To show how real wearable measurements can enter AstroGuard, this is a public research dataset recorded with a Respironics Actiwatch Spectrum Pro. These are people on Earth, not astronauts." |
| 3 | 0:50 | Point to the chart and open *What each measurement means*. | "The watch records wrist movement per minute, ambient light and whether it was worn. Its software estimates sleep or wake from movement. It does **not** measure brain-wave sleep stages, cortisol or reaction time. The banner says *Insufficient Data* because this dataset has no reaction-time tests, so we draw no alertness conclusion." |
| 4 | 1:20 | Click **Scenario 1 · Normal sleep and alertness**. Point to the HISTORICAL / SYNTHETIC DEMO strip. | "This is Mission Control with four **fictional** astronauts. Their data is synthetic and historical, from October 2026. This is not live monitoring." |
| 5 | 1:35 | Point to the green **NORMAL** banner, then open **🌙 Sleep → Sleep monitoring**. | "Bars are estimated sleep, and white markers are time in bed, which we never count as sleep. The dashed line is this astronaut's own baseline, built only from earlier nights." |
| 6 | 2:00 | Click **Scenario 2 · Shifted sleep schedule**. Show the schedule chart. | "Bedtime moved about four and a half hours later, but reaction time is still at baseline. AstroGuard flags the schedule change for monitoring. It also says a schedule shift alone does **not** prove reduced concentration." |
| 7 | 2:25 | Click **Scenario 3 · Reduced sleep, slower reactions**. | "Recent sleep is about two thirds of baseline, and reactions are about 20 % slower with more lapses. Only when **both** signals agree do we show REST RECOMMENDED." |
| 8 | 2:45 | Point to the red banner: *Data observed up to* vs *Alert created*. Open **🛰️ Overview & Decision → Decision support & evidence**. Set **Mission task context** to *Safety-Critical Operation*. | "Every rule and threshold is visible, and they are illustrative, not clinical. With a safety-critical task the wording changes to: inform the operator and consult Mission Control. AstroGuard **never** stops a task; humans decide." |
| 9 | 3:15 | Press **Acknowledge Alert**, then open **🔔 Alerts**. | "The alert was stored with its evidence. Acknowledging records who and when. It doesn't delete the alert, doesn't change the assessment, and doesn't mean the astronaut is rested. Refreshing never duplicates alerts." |
| 10 | 3:40 | Click **🧪 Live test profile**, then **⚡ Alertness → ⏱️ Reaction test** (run it if there's time). | "Live tests go to a separate profile, so they can never change the demo scenarios. The test runs in the browser, then is saved and re-evaluated. A new profile honestly shows *Insufficient Data* until it has its own baseline. We never invent sleep data." |
| 11 | 4:10 | Return to **Scenario 1**. | "This is a prototype. Next steps would be expert-validated thresholds, the standard 3-minute PVT-B, automated import of crew actigraphy files, and rolling baselines for long missions." |

## Short answers for judges

**What does Actiwatch measure?**
Wrist movement (activity counts per epoch), ambient light, off-wrist status and
event-marker presses. The Actiware software estimates sleep and wake from
movement. It does not measure EEG sleep stages, cortisol, reaction time or HRV.

**Does NASA already use actigraphy?**
Wrist actigraphy has been used in NASA-supported spaceflight sleep research. For
example, Barger et al., *The Lancet Neurology* 13(9), 2014, studied sleep in
Shuttle and ISS crew. A brief PVT ("Reaction Self Test") has been used on the
ISS. Please check these references before quoting them. AstroGuard has no NASA
endorsement, partnership or integration.

**Where does AstroGuard get its data?**
- The four astronaut scenarios use fictional, synthetic data from seeded generators.
- Real wearable data comes from a public, CC0-licensed Actiwatch dataset of Earth
  participants (Zenodo 4961281). It is shown as separate `RES-` research profiles,
  never as astronauts.
- Live reaction tests and sleep logs are saved to a separate live test profile.

**What exactly did our team develop?**
- The data model and validation, and the Actiware import adapter.
- The synthetic scenarios.
- The personalized, explainable analysis.
- Persistent, deduplicated alerts with acknowledgment.
- The browser reaction-time test.
- The Mission Control dashboard and the automated tests.

We did not build a wearable device.

**How does the system estimate alertness?**
- It compares the latest three nights of estimated sleep and the latest two
  reaction tests with the person's own baseline.
- The baseline uses only earlier observations: the first five usable nights and
  the first five completed tests.
- It also checks how far the sleep schedule has shifted.
- Each rule, its value and its threshold are shown.

**What triggers an alert?**
- **Rest Recommended** needs both signals: recent sleep below 80 % of baseline,
  *and* reaction time slower than baseline by 10 % or 2.5 standard deviations, or
  at least two more lapses per test.
- Either signal alone, or a schedule shift of 2 h or more, gives **Attention Needed**.
- Missing data gives **Insufficient Data**.
- Alerts are evaluated when data is saved or imported, when a scenario is
  selected, and when the page refreshes. All thresholds are illustrative.

**Does the watch itself beep?**
No. The optional tone plays in the Mission Control browser. AstroGuard sends no
commands to the watch and has no live connection to it.

**Does the software stop an astronaut's work?**
Never. It suggests options such as another assessment, a rest break, or
consulting Mission Control and procedures. Humans keep authority.

**Are the recommendations clinically validated?**
No. The thresholds are illustrative and configurable. The reaction test is
PVT-inspired and not a validated PVT. The demo astronauts are synthetic.

**How could it be extended?**
- Validate the thresholds with experts and study data.
- Use the standard PVT-B protocol.
- Automate imports of crew actigraphy exports.
- Use rolling baselines.
- Add light-exposure and circadian models.
- Integrate with crew-scheduling tools, with human review.

## Attribution

- Angelova M., Kusmakar S., Karmakar C., Zhu Y., Shelyag S., Drummond S., Ellis J. (2021).
  *Chronic insomnia and bed partner actigraphy data*. Zenodo record 4961281,
  doi:10.5061/dryad.b8gtht7bh, CC0 1.0. <https://zenodo.org/records/4961281>
- Background only: Dinges & Powell (1985), the PVT. Basner, Mollicone & Dinges,
  *Acta Astronautica* (2011), the brief PVT-B. Barger et al., *Lancet Neurology*
  (2014), sleep in spaceflight. AstroGuard does not implement a validated protocol.
