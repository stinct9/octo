// The core's rollback hook (esp32-hal-misc.c, a weak C function): returning true stops the core from
// marking an update good the moment it boots; recorder.ino's authorized() does it once the Mac has
// reached the new firmware, and loop() rolls back if that hasn't happened within 10 minutes.
//
// In its own .cpp because it needs C linkage: in rec-3 it sat in the .ino as a C++ function, its
// name was mangled, the override never linked, and every update was marked good at boot. (Arduino's
// prototype generator can't take extern "C" inside an .ino.)
extern "C" bool verifyRollbackLater() { return true; }
