## Switchboard 0.1.36

- **A message you send can't get lost any more:** if the connection died without anyone noticing (a network blip), what you send is kept and sent again as soon as the app reconnects, and the machine takes it only once. The app also notices a dead connection within about 40 seconds now, instead of minutes. Update the machines too (their daemons confirm each message).
