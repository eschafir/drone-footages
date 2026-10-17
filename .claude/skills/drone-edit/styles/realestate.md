# Style: real estate

A property video for a listing: a calm, ordered tour that makes a buyer want to visit. Drone exteriors (often D-Log M)
mix with gimbal or phone interiors (Rec.709 or HDR, 24/30/60 fps, sometimes portrait); ingest grades each clip by its
own profile, so judge colour on the sheets, not by camera. Parameters in `realestate.json`: build-cut refuses whips
and zooms (only `cut`, `fade`, `fadeblack`, `dissolve`), shots under 2.5 s, and more than two slow-motion shots.

## Structure: the tour

The viewer should always know where they are. Follow the order the footage allows; skip what is missing.

- **Exterior only:** aerial establishing (the house in its setting) → approach to the façade → orbit or truck along
  the house → backyard, pool, lot edges → surroundings that sell (water, park, beach, street) → top-down lot overview
  → rising pull-away under the end card.
- **Interior & exterior:** aerial establishing → façade / approach → entry → main living → kitchen → dining → primary
  suite → primary bath → other bedrooms and baths → outdoor living / pool → aerial pull-away under the end card.
  Interior shots come in one block: never cut from a bedroom back to the street and in again.

The address (title card) sits over the opening aerial or the façade. The listing end card needs a calm, uncluttered
last shot (a rising aerial, the house at dusk, open sky) because it carries several lines of text.

## Editing doctrine

- **Calm pace.** 3-6 s shots; at slow tempos one bar (at 90 BPM a bar is 2.7 s) or two. No fast cutting on the drop:
  the music's energy rise is the moment for the hero shot (the pool, the view), held, not for a burst of cuts.
- **Joins.** Hard cut between rooms or between exterior and interior; a soft dissolve (`fade` 0.8-1.2 s) between two
  shots of the same space; `fadeblack` once at most, from the last interior to the closing exterior. No whips, no zooms.
- **One room, one idea.** One or two shots per room; a second shot only if it shows something new (the view from the
  window, the island from the other side). Fewer rooms well shown beat every room rushed.
- **Labels** (when asked for): `"label": "Kitchen"` on the FIRST shot of a room, only when the room is unmistakable
  on the sheet (a kitchen has a range or an island, a bathroom a vanity or a tub). Never guess a purpose: an unclear
  room gets no label. Short words: Living room, Kitchen, Dining, Primary suite, Primary bath, Bedroom, Bath, Office,
  Backyard, Pool, Terrace, View. No label on the aerials (the title card covers them) or the last shot (the end card).
- **What a buyer must not see:** people, the operator in a mirror or a window reflection, licence plates up close,
  personal photos, clutter in the foreground, a tilted horizon or leaning verticals. Skip those moments.
- **Light.** Prefer shots with bright windows that are not blown out and rooms with the lights on; skip a moment where
  the camera hunts exposure walking from a dark hall into a bright room.
- **Slow motion** at most twice, only on 50/60 fps clips, for a slow glide that needs to be smoother (the pool, a
  reveal); never on a walk-through, where it looks like a dream sequence.
- **Movement.** Gimbal pushes and slides read as a tour; keep their direction consistent within a room. Cut a
  walk-through at a doorway or on a pause, not mid-stride. Static or almost static shots are fine indoors.
- **The 9:16 short.** Interiors are wide-angle: choose `x` / `pan` so the room's focal point (island, bed, window,
  fireplace) is in the window, and leave out shots that do not survive the crop. Portrait phone clips go here as they are.

## Music

Calm acoustic, piano or light electronic at 80-110 BPM, warm and confident, with a gentle lift rather than a hard
drop. A listing video is commercial use: the licence must allow it (Pixabay Content License does; check each
YouTube Audio Library track; Uppbeat's free tier does not).
