# MEXC Futures Fee Test Buttons

Добавлено для проверки реальной комиссии на sub-account/API.

## Кнопки

- `Limit Price` — открывает BTCUSDT + ETHUSDT long лимитными ордерами рядом с рынком.
- `Market Price` — открывает BTCUSDT + ETHUSDT long market-ордерами.

Обе кнопки используют реальные MEXC futures endpoints через `https://api.mexc.com` и требуют API key с Futures permissions.

## Размер

На каждый символ:

```text
margin = 3 USDT на символ
leverage = 4x
notional = margin * 4 ≈ 12 USDT на символ
side = long
openType = isolated
```

BTCUSDT и ETHUSDT открываются сразу в рамках одного теста. Через 3 минуты бот пытается закрыть обе позиции.

## Limit-mode

Лимитка ставится рядом с текущей ценой так, чтобы с высокой вероятностью исполниться:

- open long: buy limit немного выше best ask;
- close long: sell limit немного ниже best bid;
- если close-limit не исполнился, бот делает market fallback close, чтобы не оставить позицию.

Это сделано именно для fee-test, а не для торговой оптимизации.

## Логи

Команда:

```text
/log_mexc
```

Отправляет:

- `mexc_fee_test.jsonl` — полный сырой JSON log;
- `mexc_fee_test.csv` — краткая таблица fills/fees.

Путь в контейнере:

```text
storage/logs/mexc_fee_test.jsonl
storage/logs/mexc_fee_test.csv
```

В лог пишется:

- test_id;
- order_id / external_oid;
- symbol;
- open/close;
- requested vol;
- filled vol;
- avg price;
- fee;
- fee currency;
- taker flag;
- raw API response;
- final open positions.

## Safety

Это тестовый бот. Он открывает реальные сделки. Перед запуском проверь, что API key принадлежит нужному sub-account и имеет только нужные futures permissions.

## v15 fee-test hotfix

- Fixed MEXC error `2030 External order ID too long`: `externalOid` is now compact, for example `mob_27de5be20f12`.
- Plan logs now include `actual_notional_usdt`, `actual_margin_usdt`, `target_margin_usdt`, and `min_vol_forced`.
- Note: if account balance is very small, MEXC minimum contract volume can force actual margin above the requested 3 USDT margin per symbol. In that case top up the sub-account or reduce the test symbols manually.


## v15.1 fixed-size hotfix

- Fee-test now uses fixed `3 USDT` margin per symbol.
- Leverage is `4x`, so target notional is about `12 USDT` per symbol.
- Auto-close delay is `3 minutes`.
- BTCUSDT and ETHUSDT are still opened together as LONG.
- Because futures contracts have minimum volume, actual margin can be slightly above 3 USDT, especially on ETHUSDT. Check `actual_margin_usdt` in `/log_mexc`.
