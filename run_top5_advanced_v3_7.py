import sys
mode=(sys.argv[1].lower() if len(sys.argv)>1 else "midday")
print("=== TOP5 ADVANCED GPT V3.7 AUTO |",mode.upper(),"===",flush=True)
if mode=="morning":
    print("05:00 - EOD QUALITY GATE - KHONG BUILD 544",flush=True)
    import daily_valuation_v3; daily_valuation_v3.main()
    import financial_score_v3; financial_score_v3.main()
    import technical_engine_v3; technical_engine_v3.main()
    import entry_engine_v3_4; entry_engine_v3_4.main()
    import candidate_gate_v3_5; candidate_gate_v3_5.main()
    import top5_advanced_v3; top5_advanced_v3.main()
elif mode=="midday":
    print("11:31 - GIU EOD TOP, CHI CHECK KBS REALTIME",flush=True)
    import midday_advanced_v3_6; midday_advanced_v3_6.main()
else:
    raise SystemExit("Mode chi nhan morning/midday")
import send_top5_advanced_v3_7_telegram as tg
tg.main(mode)
print("\nDONE V3.7",mode,flush=True)
