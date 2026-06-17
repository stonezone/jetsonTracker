# Molotov Paperboy

A faithful, re-skinned clone of the 1985 Atari arcade classic **Paperboy** — same
gameplay loop, new theme. Instead of a paperboy on a BMX tossing newspapers into
mailboxes, you're a kid on a glowing **Sirron** board lobbing **Molotov cocktails**
at the **Cyber Trucks** parked in each subscriber's driveway.

Single self-contained file — open `index.html` in any modern browser. No build, no deps.

## How it maps to Paperboy

| Paperboy (1985)                  | Molotov Paperboy                                  |
|----------------------------------|---------------------------------------------------|
| Paperboy on a BMX bike           | Kid on a **Sirron** board                         |
| Newspapers                       | **Molotov cocktails**                             |
| Subscriber's **mailbox** (250)   | Cyber Truck **bed** (250)                          |
| Subscriber's **porch** (100)     | Cyber Truck **driveway** (100)                     |
| Break non-subscribers' windows   | Smash non-subscribers' windows (Breakage Bonus)    |
| Paper bundles to restock         | Molotov crates to restock                          |

## Gameplay (the Paperboy loop)

- You auto-ride down the street; **subscriber houses glow** (Cyber Truck in the drive),
  **non-subscribers are dark** (boarded windows). Houses line **both sides**.
- **Deliver** to every subscriber: lob a Molotov onto their Cyber Truck. Landing it in the
  **truck bed = 250**, the **driveway = 100**. Throw as the house comes alongside you.
- **Smash non-subscribers' windows** for points that tally into the end-of-day **Breakage Bonus**.
- **Miss a subscriber** and you lose them the next day. **Overthrow onto a subscriber's house**
  (instead of the truck) damages their property — you lose them too. Deliver to **every**
  subscriber in a day and **one non-subscriber re-subscribes**.
- **Limited Molotovs** (start with 10). Ride over **Molotov crates** for +10.
- **Dodge the traffic**: hover-cars, rolling tires, robo-dogs, drones, barriers. A crash costs a life.
  Dawdle too long and the **bees** swarm you (just like the arcade).
- Survive the week: **Mon → Sun** (Easy Street → Middle Road → Hard Way), each day ending in a
  **BMX bonus course** — hit ramps and targets, cross the finish line for a bonus, then onto the next day.
- 3 lives. Game over when they run out, or when you lose every subscriber.

## Controls

| Action            | Keys                                  |
|-------------------|---------------------------------------|
| Steer             | **← / →**                             |
| Speed up / slow   | **↑ / ↓**                             |
| Throw **left**    | **Z**                                 |
| Throw **right**   | **M**                                 |
| Throw nearest side| **SPACE**                             |

Touch devices get on-screen steer, slow, and two throw buttons.
