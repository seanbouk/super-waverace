/*---------------------------------------------------------------------------------
    Super Waverace — phase 1: hello world boot test
---------------------------------------------------------------------------------*/
#include <snes.h>

int main(void)
{
    // Text console: default map at 0x6800, gfx at 0x3000
    consoleInitDefaultText(0);

    bgSetGfxPtr(0, 0x3000);
    bgSetMapPtr(0, 0x6800, SC_32x32);

    // 16-colour mode, only BG1 enabled
    setMode(BG_MODE1, 0);
    bgSetDisable(1);
    bgSetDisable(2);

    consoleDrawText(9, 12, "SUPER WAVERACE");
    consoleDrawText(8, 16, "HELLO FROM PHASE 1");

    setScreenOn();

    while (1)
    {
        WaitForVBlank();
    }
    return 0;
}
