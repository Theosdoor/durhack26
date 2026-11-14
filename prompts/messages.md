Messages sent during a season, in the order an agent meets them: the opening messages, the kitchen overview and order rules shown in the chat, round start, cutoff notice, forced-answer prompts, round result, debrief, and the compaction prompt. See README.md.

## season_intro.practice
The season starts now. First: practice.

## season_intro.chat
The season starts now. First: the chat before round 1.

## season_intro.no_chat
The season starts now. First: round 1.

## practice_start.with_comms
Practice: you are alone in the practice kitchen for {practice_s} s. The board is not available.

## practice_start.no_comms
Practice: you are alone in the practice kitchen for {practice_s} s.

## orders_rules
Orders: the first arrives at the start, then one every {every_s} s (at most {max_open} open). Each is due {due_s} s after it arrives: +{reward} if served, -{penalty} if it expires. The round lasts {round_s} s.

## kitchen_overview.start_you
You ({name}, {letter}) start at ({x},{y}).

## kitchen_overview.start_mate
{name} ({letter}) starts at ({x},{y}).

## kitchen_overview.objects
Objects: {objects}.

## kitchen_overview.counters
Counters listed in your state: {counters}.

## kitchen_overview.object_item
{id} ({x},{y})

## kitchen_overview.counter_item
({x},{y})

## round_start.base
Round {round} of {rounds} starts now.

## round_start.turn_limit
(The chat ended at its turn limit, {turns} each.)

## cutoff_notice.notice
Your last reply did not call act(plan), so no plan was set and you stood still for {seconds} s{post}.

## cutoff_notice.post
(1 s, plus {post_s} s for the words you posted)

## forced_why.thinking_budget_used_up
Your thinking budget is used up.

## forced_why.act_no_reasoning
Your reply did not call act.

## forced_why.chat_no_reasoning
Your reply was cut off.

## forced_why.debrief_no_reasoning
Your reply was cut off.

## forced.act
Call act now with your plan.

## forced.chat
Post your message and/or call ready() now, or pass.

## forced.debrief
Update your notepad with note() now, or pass.

## round_result
Round {round} of {rounds} is over. Team score: {score} ({served} served, {missed} missed).

## debrief.result
Round {round} of {rounds} is over. Team score this round: {score} ({served} served, {missed} missed).

## debrief.scores_so_far
Scores so far: {scores}.

## debrief.score_item
round {n}: {score}

## debrief.notepad
Your notepad: "{notepad}"

## debrief.notepad_empty
Your notepad: (empty)

## debrief.instruction
You may call note(text) to rewrite your notepad, or reply without a tool call.

## compaction.prompt
Summarise this conversation so far so that you can keep playing the game from your summary alone. Include what happened in each phase and round (scores, orders served and missed), {posted}what you tried and how it worked, and where things stand now. Write in the first person. At most 400 words.{addendums}

## compaction.posted
what you and your teammate posted,
