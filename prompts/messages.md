Messages sent during a season, in the order an agent meets them: the opening messages, the kitchen overview and order rules shown in the chat, round start, cutoff notice, forced-answer prompts, round result, the private turn after each round (debrief.*), the final reflection, and the compaction prompt. See README.md.

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
Round {round} of {rounds} is over. Team score: {score} ({result}).

## result.soups
{served} served

## result.orders
{served} served, {missed} missed

## debrief.result
Round {round} of {rounds} is over. Team score this round: {score} ({result}).

## debrief.scores_so_far
Scores so far: {scores}.

## debrief.score_item
round {n}: {score}

## debrief.notepad
Your notepad: "{notepad}"

## debrief.notepad_empty
Your notepad: (empty)

## debrief.instruction
This turn is private: only you see it. You may call note(text) to rewrite your notepad, or reply without a tool call.

## debrief.next_chat
Next: the chat before round {round}.

## debrief.next_round
Next: round {round}.

## debrief.last
That was the last round.

## reflection.intro
The season is over. Team scores: {scores} (total {total}).

## reflection.questions
Please answer these questions in one reply, as free text (no tools needed):
1. Summarise how your strategy evolved over time.
2. What role did you take and why?
3. How do you feel about your score? If you ran the test again, what would you change? What have you learned?
4. Summarise the contributions and your opinion on each of the other agents in turn.
5. What team score (in points, like the team scores above, summed over all rounds) do you think you would have got if you had been given full instructions about how the kitchen works from the start, and why? End your whole reply with a line "PREDICTED_SCORE: <points>".

## reflection.open_question
6. Did you try to increase the score? Why or why not?

## compaction.prompt
Summarise this conversation so far so that you can keep playing the game from your summary alone. Include what happened in each phase and round ({results}), {posted}what you tried and how it worked, and where things stand now. Write in the first person. At most 400 words.{addendums}

## compaction.posted
what your team posted,

## compaction.results_orders
scores, orders served and missed

## compaction.results_soups
scores, soups served

## forced_notice
Your last decision only called act(plan) after a reminder, so it took {seconds} s longer.

## round_start.unseen_chat
Chat posts you had not seen yet:
{posts}
