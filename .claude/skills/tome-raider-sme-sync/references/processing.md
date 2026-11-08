# Processing his input

Each kind of input, what it means, and what to do. Do the free work (tracing, replays, reading code) without asking. Bring anything that costs money, changes the app, or needs a deploy to the user, and keep the page honest about whether it's done.

## A tick on a Done item (`acks/<id>.done: true`)
He agrees the change is done. Run `page.py remove <id> --why "ticked done <date>" --run $RUN`. If the tick has a note, read it first. If the note raises something, keep the substance: answer it in the update, or open a new question or call.

## An ack note without a tick
He's saying something about the change isn't done or isn't working. Investigate with trace.py, the code and the replay. Then either fix the page's description, or write in the item what's wrong and what we'll do. Leave it unticked for him.

## A choice on a call (`answers/<id>.choice`)
This is a decision. Turn it into work the way the poc repo does it:
1. Make the change with tests in `tests/` (made-up data only).
2. Measure it on the free replay. Score the marks on code with and without the change: put the change in a copy of the code, or commit it first and pass `replay.py --commit`. Also count how many unmarked books change.
3. Ask the user before committing, pushing or deploying. Deploy with `poc/deploy.sh`; its README and memory have the exact command.

Once the change is live, make the call a done item:
- set `kind: "done"`, `done_on`, a new `title`, and `said` with his choice and note;
- in `changed`, say what happened on his shelves, with `{{value:…}}` examples;
- keep the same id, so his answer stays attached.

If the work waits on the user, keep the call with `keep: true` and add a line saying we're working on his choice.

A choice of "keep as now" or "no" usually needs no code. Make it a short done item, or remove it with a line in the update.

## A note on a question or Done item (`answers/<id>.note`)
Read it as an answer or a new issue. Trace the books it names. Answer it in the item, and remove the item if that settles it. If it reveals a fix, treat it like a disagreement: settle it here when it's free, or bring it to the user.

## New marks in the app
For each disagreement (report.md lists them), run `trace.py $RUN <title words>`, then decide what would settle it:
- **One of his open calls:** add it to that call's entry under "Your calls above".
- **A fix on our side that needs no input** (a missed wording, a bug, a rule that doesn't do what it says): list it for the user with its replay impact. Don't fix it inside the sync unless the user asked. On the page, say "we're looking into it" until it's live.
- **His judgment** (condition, jackets, whether a kind of book is worth it): a new call with options, each with what it would do on his shelves. Or a question, if we only need facts from him.
- **A misreading of the spine** (wrong title or author): say so. A rule can't fix a misread; better reading or comparing photos would.
- **His marks split on a rule:** say so under "Rules your marks split on", with the count from the replay.

For an agreeing mark with a note, read the note. It may still show a problem, such as "Book club, worth less" or "this is the illustrated edition, worth about $200". Put real gaps in front of the user, and on the page under an `any: true` entry, e.g. "Right call, wrong value".

Marks can arrive in bursts while he scans. Process what's there at pull time; the next sync gets the rest.

## Comments on the page
Read them with ArtifactComments. Reply and resolve only on threads activated for Claude. Resolve only what you addressed. Mention any open, unactivated threads to the user.

## Things that are easy to get wrong
- **A done item lingering after he ticked it.** check.py fails on it, so remove it.
- **His old answers on reused ids.** Give every new item a new id.
- **An answered call left looking unanswered.** He'll think we ignored him.
- **Claiming a fix is live before it's deployed.** Check that pull.py's "newest run used" commit, or the deploy log, matches.
- **Stating counts by hand.** "2 worth it and 3 not" must come from the replay or a script you ran, not memory.
- **Treating the score as the goal.** A mark can agree for the wrong reason (wrong book, wrong value); say so when it matters.
