module legacyagent

go 1.16

// Do NOT raise this directive, and do not build this agent with a modern toolchain.
// It cross-compiles to windows/386 for guests as old as Windows XP, and only Go 1.10.x
// stamps PE MinSubsystemVersion 4.0; Go >= 1.11 stamps 6.1 and XP refuses to load the
// binary ("Exec format error"). build.sh checks the header and fails rather than ship a
// dud exe. renovate.json disables the gomod manager for this repo for the same reason.
