# The CampusCare Assistant

*GENERATED FILE — do not edit by hand.*
*Regenerate with:* `python scripts/dump_assistant_topics.py`

The assistant is the chat panel behind the **Need help?** button, available on
every page once signed in. It answers from the database, scoped to whoever is
asking, and it is **not** a language model — see the module docstring in
`app/services/chatbot.py` for why that is a deliberate choice rather than a
limitation.

This file lists everything it can answer. The same list is browsable inside the
app: open the assistant and press the **☰** button in its header.

## How a message is resolved

Four stages, stopping at the first that produces an answer:

1. **A complaint code** anywhere in the message wins outright — `CC102`,
   `cc-102`, `complaint 102`. A code the asker is not entitled to see is
   answered as *not found*, revealing nothing about it.
2. **An exact pattern** from the catalogue below.
3. **Keyword scoring** across the catalogue. A confident match is answered
   directly; a weak one is answered but prefixed with *"I am not certain I
   understood…"*, so a wrong turn is obvious.
4. **Problem detection.** A message describing something broken is treated as
   a report rather than a question: the assistant names the category and
   priority the wording suggests, says plainly that a complaint typed into the
   chat reaches nobody, and links to the form.

Only when all four find nothing does it say so — and even then it offers the
closest questions it does know, plus a route to a person.

## What it will never do

- Reveal a complaint the asker cannot already open in the UI. Scoping lives in
  one function, `_own_complaints`, and code lookups reuse the same
  `Complaint.is_visible_to` rule the complaint page uses.
- Invent a status, a name, or a date. When it does not know, it says so.
- Change anything. It has no write path — it cannot file, assign, close or
  message on anyone's behalf.
- Store the conversation. The transcript lives in the browser tab and is gone
  when the tab closes.

## What a student can ask

41 topics across 9 groups.

### Tracking your complaints

**Who is handling it**
- “Who is handling my complaint?”
- “Which staff member has it?”

**The full history of a complaint**
- “Show the history of my complaint”
- “What has happened to my complaint?”

**How many you have**
- “How many complaints do I have?”
- “How many are still open?”

**Your most recent complaint**
- “What is my latest complaint?”
- “Show my last complaint”

**Filtering by status**
- “Show my open complaints”
- “Which of my complaints are closed?”

**All of your complaints**
- “Show my complaints”
- “What is the status of my complaints?”

### Filing a complaint

**What to write in a complaint**
- “What should I write in the description?”
- “How much detail?”

**Editing or deleting a complaint**
- “Can I edit my complaint?”
- “How do I delete a complaint?”

**Somebody already reported it**
- “Someone else already reported this”
- “Will my complaint be a duplicate?”

**Which category to choose**
- “Which category should I pick for a broken fan?”
- “What category is a leaking tap?”

**Filing a complaint**
- “How do I file a complaint?”
- “How do I report a problem?”

**Choosing a priority**
- “What does Urgent mean?”
- “Which priority should I choose?”

**Locations you can pick**
- “What locations can I choose?”
- “My room is not in the list”

**The categories available**
- “What categories are there?”
- “What kinds of problems can I report?”

### Photos and evidence

**A photo that will not upload**
- “My photo will not upload”
- “Why was my photo rejected?”

**Adding photos afterwards**
- “Can I add photos later?”
- “Can I attach more evidence now?”

**Attaching photos**
- “How do I attach photos?”
- “What image formats are allowed?”

### How the process works

**What a status means**
- “What does Pending mean?”
- “What are all the statuses?”
- “What does Student Verification mean?”

**What happens after you submit**
- “What happens after I submit?”
- “How does the whole process work?”

**Confirming a repair**
- “What happens when it is resolved?”
- “It is still broken, what do I do?”
- “How do I reopen a complaint?”

**How complaints get assigned**
- “Who assigns complaints?”
- “How is my complaint routed?”

**Notifications**
- “Where are my notifications?”
- “How will I know when it updates?”

**Email updates**
- “Will I get an email?”
- “I did not receive any email”

### Timing and deadlines

**Overdue complaints**
- “Is anything overdue?”
- “Has my complaint missed its deadline?”

**Escalating a stalled complaint**
- “How do I escalate?”
- “Nobody is doing anything about it”

**When it will be fixed**
- “When will my complaint be fixed?”
- “What is the deadline?”

**Why it is taking so long**
- “Why is it taking so long?”
- “Nobody has responded yet”

### Your account

**Staff account approval**
- “My staff account is pending”
- “When will my account be authorised?”

**Two-step verification**
- “What is the OTP for?”
- “My verification code has not arrived”

**Registering an account**
- “How do I register?”
- “How do I create an account?”

**Your own details**
- “What are my account details?”
- “What is my role?”

**Passwords and signing in**
- “I forgot my password”
- “How do I sign out?”

### Privacy and safety

**Something dangerous**
- “There is a live wire hanging in the corridor”
- “What do I do in an emergency?”

**Who can see your photos**
- “Who can see my photos?”
- “Are my photos public?”

**Who can see your complaint**
- “Can I file anonymously?”
- “Who can see my complaint?”

**How your data is protected**
- “Is my data safe?”
- “How secure is this system?”

### For staff

**Resolving an assigned complaint** *(only shown to staff, admin)*
- “How do I resolve a complaint?”
- “How do I upload a repair photo?”

### About CampusCare

**Everything you can ask**
- “What can I ask you?”
- “Show me the list of questions”

**Reaching a person**
- “How do I contact an administrator?”
- “I want to talk to someone”

**What CampusCare is**
- “What is CampusCare?”
- “What is this system for?”

**Open source and technology**
- “Is this open source?”
- “What technology is it built with?”

## Recognised without being listed

Handled, but kept out of the menu because nobody browses for them:

- **What this assistant is** — e.g. “Are you a real person?”
- **When nothing is happening** — e.g. “This is useless”
- **Saying hello** — e.g. “Hello”
- **Saying thanks** — e.g. “Thanks”
- **Saying goodbye** — e.g. “Bye”
- **What I can help with** — e.g. “What can you do?”

## Adding a topic

Add one `Topic(...)` to `TOPICS` in `app/services/chatbot.py`, carrying:

| Field | Purpose |
| --- | --- |
| `key` | Stable identifier, also the `intent` in the JSON reply |
| `group` | Heading it appears under, from `GROUP_ORDER` |
| `title` | Short label in the browsable menu |
| `examples` | Questions a student would actually type — the first is the one shown |
| `keywords` | Softer signals for the keyword-scoring fallback |
| `pattern` | The precise regular expression |
| `handler` | Function returning an `Answer` |
| `roles` | Optional: restrict to `staff`/`admin` |

Order matters — the list is checked top to bottom, so a new broad pattern
placed too early will steal questions from narrower topics below it. The menu,
this document and the fallback all update themselves from that one row.

Then run the tests: `test_every_advertised_question_reaches_its_own_topic`
fails if the new pattern steals anything, or if anything steals from it.
