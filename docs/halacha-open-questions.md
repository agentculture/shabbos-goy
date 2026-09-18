# Open halachic questions

This file lists questions this project does **not** answer. It records them so
they stay visible, so a user can put them to their own rav, and so nobody
mistakes an engineering decision for a halachic one.

There is **no rabbinic approval (*hechsher*)** for this software, and none is
claimed. Nothing here is a ruling, a leniency, or a recommendation. Where the
text says "this is what the code does", that is a description of the code, not
an argument that the code is permitted.

Every one of these questions is a reason to ask before using the agent, not a
list of settled points with a footnote attached.

## How the configuration relates to these questions

The action whitelist, the location and zmanim rules, the delay and the rate
limits are all **data** (`$XDG_CONFIG_HOME/shabbos-goy/config.json`), not code.
A household that is told to narrow any of them can narrow them without
touching this repository. That is a property of the software, not a claim that
any particular setting is permitted.

## The questions

### 1. What is the device, halachically?

Does an always-listening agent that infers a wish and switches a mains-powered
appliance count as a *shabbos goy* (a non-Jew acting on a hint), as a Shabbat
clock/timer set before the day, as *grama* (indirect causation), as a
*melacha* performed by the household, or as none of these? The name of this
project is the familiar Yiddish term for the human role; it is not a claim
that the analogy holds.

### 2. Is speaking near an always-listening device a problem in itself?

The agent has no wake word: on Shabbat and Yom Kippur it listens
continuously. Is an ordinary remark made in a room where a device is known to
be listening for hints still an ordinary remark? Does it matter whether the
speaker knows the device is there, or whether they intended it to hear?

### 3. Which hints, if any, are permitted?

Classical discussions of hinting distinguish need, illness, heat that is more
than discomfort, a *mitzvah*, a child, a guest. This agent's whitelist today
is not narrowed by any of that: any utterance the model labels a remark, a
wish, or a statement of discomfort about the temperature of this room is
treated the same way. Is "I wish it was cold" said out of ordinary preference
in the same category as the same words said by someone unwell? Should the
whitelist be narrower than "anything the household would like"?

### 4. Powering the air conditioner OFF from a "cold" hint

Turning a device on and turning it off are not obviously the same question.
This agent maps a hot hint to power ON and a cold hint to power OFF, so a
remark can cause a *cessation* of an appliance that was running when Shabbat
began. Is that a different question from causing it to start? Does it change
if the unit was started by a timer, or by hand before candle lighting?

### 5. The deliberate delay

In strict mode the agent waits a configurable interval (default about 15
seconds) between deciding and acting. The delay exists in the code for
engineering reasons — a second speaker may contradict the first, and an
immediate reaction to speech looks like obedience to it. Whether a delay makes
any halachic difference at all, whether it is a form of *grama*, and whether a
*longer* delay would be better or worse, are questions for a rav and not for
this repository. The delay is config, so it can be changed or set to zero.

### 6. Using the dashboard or the CLI on Shabbat or Yom Tov

The dashboard and the command line are **operator tools**. They are reachable
in every mode, including strict mode, and their buttons can switch AC power,
change volume, force strict mode on, or switch it off inside a zmanim window.
That is a deliberate engineering decision about what the software permits; it
says nothing about whether a person may press those buttons on Shabbat or Yom
Tov. This project makes no claim that the dashboard is usable on a holy day.
The intended use is that the operator sets everything up **before** candle
lighting and touches nothing afterwards.

### 7. A language model making the judgement

The decision of what kind of speech was heard is made by a language model
running locally (the lobes `senses` role), not by a fixed rule table. This
repository's code still refuses anything labelled a command, and still
enforces the mode gate, the whitelist, the argument validation, the confidence
floor, the rate limits and the delay. But the *judgement* — is this a hint or
an instruction — is a model's, and it is not fully predictable in advance.
Whether that matters halachically, and whether an unpredictable judge is
better or worse than a rule table, is an open question. The engineering answer
is only that the model's output is treated as untrusted input and that the
refusals are measured (see `tests/golden/README.md`).

### 8. Yom Kippur

The agent treats Yom Kippur with the same strict window as Shabbat. Are the
relevant questions the same? Is cooling a room a different matter on a fast
day, where discomfort is part of the day rather than something to be relieved?

### 9. Yom Tov

Yom Tov days use the strict column by default, and whether the second day is
observed follows the `region` setting in config (Israel or diaspora).
Yom Tov has leniencies Shabbat does not, in particular around fire and
cooking. This project does **not** implement any of them: Yom Tov behaves
exactly like Shabbat here. Whether that is too strict, and whether a household
should want it to be different, is a question for a rav.

## What is not on this list

Practical engineering questions — whether the model mislabels a sentence, what
happens after a power cut, whether the microphone hears through a wall — are
not halachic questions and are handled in the code and its tests. See
`README.md` and `CLAUDE.md`.
