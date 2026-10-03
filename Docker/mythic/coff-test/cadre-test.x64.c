// Minimal Apollo execute_coff test object (standard BOF ABI).
// NOTE: Beacon imports MUST be __declspec(dllimport): Apollo's COFFLoader
// only resolves `__imp_Beacon*` symbols (see process_symbol() in
// COFFLoader.c). A plain declaration emits an unprefixed reference the
// loader returns NULL for -> "RunCOFF failed with status: 1".
__declspec(dllimport) void BeaconPrintf(int type, char *fmt, ...);

void go(char *args, int len) {
    BeaconPrintf(0, "cadre-coff-ok");
}
