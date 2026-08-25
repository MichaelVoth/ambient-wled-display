# Visual Language

A strip is easiest to read when each visual property has a stable job.

- **Position** answers where or how much. Put the most immediate information at
  the physical top and grow counts downward.
- **Motion** answers what changed. Upward can mean arrival or recovery;
  downward can mean departure or completion.
- **Color** answers what kind. Keep categories consistent rather than assigning
  a new color to every automation.
- **Duration** answers urgency. Ambient context can persist; notifications
  should appear briefly and restore the previous look.

## Suggested vocabulary

| Signal | Treatment |
| --- | --- |
| Hour | Sweep to darkness, then cumulative top-down bell-toll dots |
| Rain | Persistent slow blue movement |
| Lunch or calendar reminder | Calm amber beacon in the visible top third |
| Task complete | Brief green upward wave |
| Workday complete | Multicolor one-dimensional fireworks |
| Service unhealthy | Brief amber pulse; red only when urgent |
| High energy use | Amber band proportional to excess |
| Someone arriving | Brief upward sweep |
| Quiet or focus time | Lower brightness and reduced motion |

Layers may be combined when they use independent channels—for example, a blue
ambient palette can communicate rain while a short motion communicates an
arrival. Avoid assigning incompatible meanings to the same channel. If red
sometimes means weather, sometimes a meeting, and sometimes a failure, it
stops being glanceable.

## Implemented colors

The renderer now reserves four semantic color families:

- **Blue/cyan** is persistent environmental context. Rain cools the base
  palette and adds narrow downward-moving cyan drops.
- **Amber** asks for attention without implying danger. Lunch reminders stay
  in the top third; warnings pulse across the lane.
- **Green** means a successful completion and travels upward.
- **Red** is only used by the urgent alert. It is intentionally absent from
  ordinary reminders and warnings.

The work celebration is the exception: its meaning is carried mainly by its
launch-and-burst motion, so it may use several festive colors without weakening
the meaning of the status colors above.
