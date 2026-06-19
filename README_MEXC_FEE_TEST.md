# MEXC Futures Fee Test Buttons

Добавлено для проверки реальной комиссии на sub-account/API.

## Кнопки

- `Limit Price` — открывает BTCUSDT + ETHUSDT long лимитными ордерами рядом с рынком.
- `Market Price` — открывает BTCUSDT + ETHUSDT long market-ордерами.

Обе кнопки используют реальные MEXC futures endpoints через `https://api.mexc.com` и требуют API key с Futures permissions.

## Размер

На каждый символ:

```text
margin = 10% от USDT equity
leverage = 2x
notional = margin * 2
side = long
openType = isolated
```

BTCUSDT и ETHUSDT открываются сразу в рамках одного теста. Через 5 минут бот пытается закрыть обе позиции.

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
