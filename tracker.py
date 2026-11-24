for item in products:
            try:
                scraped_title, text = get_page_info(item["url"])
                print(item["name"], "len:", len(text), flush=True)
                if len(text) < 800:
                    continue

                prompt = f"""
                Analyze this Indian e-commerce product page text for "{item['name']}".

                STRICT EXCLUSION RULE:
                - Completely IGNORE and EXCLUDE any offers, discounts, cashback, or No Cost EMI plans that are ONLY for "Amazon Pay ICICI Credit Card" or "Bajaj Finserv / Bajaj Finance EMI Card".
                - Only consider standard bank Credit/Debit cards (like HDFC, SBI, ICICI regular cards, Axis, Kotak, IDFC, BOB, OneCard, etc.).

                Extract these exact fields:
                1. "product_title": Clean short product title (max 60 chars) from the page.
                2. "base": Current selling price in INR as an integer (ignore MRP/strikethrough).
                3. "coupon": On-page clickable coupon/voucher discount in INR as an integer (e.g., "Apply ₹2,000 coupon", "Save ₹500 with coupon"). Put 0 if none.
                4. "full_discount": Best instant bank/card discount amount in INR on FULL PAYMENT (one-time payment, excluding Amazon Pay ICICI & Bajaj Finance). Put 0 if none.
                5. "full_offer_text": Short summary of eligible banks/cards for full payment discount (excluding Amazon Pay ICICI & Bajaj Finance, or "None").
                6. "nc_emi_months": Maximum No Cost EMI tenure in months available on eligible bank cards (e.g., "6 Months", "12 Months", "18 Months", "24 Months", or "None").
                7. "nc_emi_interest_discount": Maximum interest discount amount in INR (as a number) available on eligible bank cards for No Cost EMI (excluding Amazon Pay ICICI & Bajaj Finance). Put 0 if none.
                8. "nc_emi_banks": Short list of eligible banks for this No Cost EMI offer (excluding Amazon Pay ICICI & Bajaj Finance, or "None").

                Return ONLY valid JSON with these exact keys:
                {{"product_title": "string", "base": number, "coupon": number, "full_discount": number, "full_offer_text": "string", "nc_emi_months": "string", "nc_emi_interest_discount": number, "nc_emi_banks": "string"}}

                Page Text:
                {text[:35000]}
                """

                data = ask_ai(prompt)
                if not data or not data.get("base"):
                    continue

                if item["name"] in ("Product", "", None) or len(item["name"]) < 4:
                    ai_title = (data.get("product_title") or "").strip()
                    if ai_title and ai_title.lower() != "product":
                        item["name"] = ai_title[:60]
                    elif scraped_title and scraped_title.lower() != "product":
                        item["name"] = scraped_title[:60]

                base = int(round(data.get("base", 0)))
                coupon = int(round(data.get("coupon", 0)))
                full_disc = int(round(data.get("full_discount", 0)))
                net = max(0, base - coupon - full_disc)
                emi_disc = int(round(data.get("nc_emi_interest_discount", 0)))
                emi_net = max(0, base - coupon - emi_disc)

                data["base"] = base
                data["coupon"] = coupon
                data["full_discount"] = full_disc
                data["net"] = net
                data["nc_emi_interest_discount"] = emi_disc
                data["emi_net"] = emi_net

                print(item["name"], "->", data, flush=True)
                prev = history.get(item["url"])

                is_new = prev is None
                changed = False
                if prev:
                    prev_coupon = prev.get("coupon", 0)
                    prev_net = prev.get("net", net)
                    prev_base = prev.get("base", base)
                    prev_emi_net = prev.get("emi_net", emi_net)
                    if (
                        net < prev_net
                        or base < prev_base
                        or coupon != prev_coupon
                        or emi_net < prev_emi_net
                    ):
                        changed = True

                if is_new or changed:
                    header = "🆕 *INITIAL PRICE & OFFERS*" if is_new else "🚨 *PRICE / OFFER UPDATE*"
                    coupon_line = f"• *Coupon Discount:* ₹{coupon:,}\n" if coupon > 0 else ""
                    msg = (
                        f"{header}\n\n"
                        f"*{item['name']}*\n"
                        f"• *Base Price:* ₹{base:,}\n"
                        f"{coupon_line}\n"
                        f"💳 *Full Payment Offer:*\n"
                        f"• *Bank Discount:* ₹{full_disc:,} ({data.get('full_offer_text', 'None')})\n"
                        f"• *Final Net Price:* *₹{net:,}*\n\n"
                        f"📅 *No Cost EMI (Excl. Amazon ICICI & Bajaj):*\n"
                        f"• *Max No Cost Tenure:* {data.get('nc_emi_months', 'None')}\n"
                        f"• *Interest Discount:* ₹{emi_disc:,} ({data.get('nc_emi_banks', 'None')})\n"
                        f"• *Net Cost (Base - Coupon - Interest):* *₹{emi_net:,}*\n\n"
                        f"[Open Product Link]({item['url']})"
                    )
                    send_tg(
                        msg,
                        chat_id=item.get("chat_id", DEFAULT_CHAT_ID),
                        reply_to=item.get("reply_to"),
                    )

                history[item["url"]] = data
                time.sleep(1)
            except Exception as e:
                print(item["name"], "err:", e, flush=True)
