FUNCTION_BLOCK FB_StartStop
VAR_INPUT
    StartButton : BOOL;
    StopButton : BOOL;
END_VAR
VAR_OUTPUT
    Motor : BOOL;
END_VAR

IF StopButton THEN
    Motor := FALSE;
ELSIF StartButton THEN
    Motor := TRUE;
ELSE
    Motor := Motor;
END_IF;
END_FUNCTION_BLOCK
