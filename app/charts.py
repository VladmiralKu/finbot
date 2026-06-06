import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from datetime import datetime


async def generate_monthly_chart(user_id: int, year: int, month: int) -> bytes:
    from app.database import fetchall, get_monthly_summary

    MONTHS_RU = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
                 7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}

    # Данные за последние 6 месяцев для верхнего графика
    months_data = []
    for i in range(5, -1, -1):
        m = month - i
        y = year
        while m <= 0:
            m += 12
            y -= 1
        s = await get_monthly_summary(user_id, y, m)
        months_data.append({
            'label': MONTHS_RU[m][:3],
            'income': s['income'],
            'expense': s['total_expense'],
            'balance': s['balance'],
        })

    # Данные по категориям за текущий месяц
    cats = await fetchall(
        """SELECT c.name, SUM(t.amount) as total
           FROM transactions t
           JOIN categories c ON t.category_id = c.id
           WHERE t.user_id = %s AND t.type = 'expense'
             AND EXTRACT(YEAR FROM t.transaction_date) = %s
             AND EXTRACT(MONTH FROM t.transaction_date) = %s
             AND c.kind NOT IN ('depreciation', 'tax', 'loan_body', 'loan_pct')
           GROUP BY c.name
           HAVING SUM(t.amount) > 0
           ORDER BY total DESC""",
        (user_id, year, month)
    )

    # Остатки за последние 6 месяцев
    balances = [d['balance'] for d in months_data]
    labels = [d['label'] for d in months_data]

    # Настройка графика
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(8, 12))
    fig.patch.set_facecolor('#1a1a2e')

    for ax in [ax1, ax2, ax3]:
        ax.set_facecolor('#16213e')
        ax.tick_params(colors='white')
        ax.spines['bottom'].set_color('#444')
        ax.spines['top'].set_color('#444')
        ax.spines['left'].set_color('#444')
        ax.spines['right'].set_color('#444')

    # --- Верх: Приход / Расход ---
    x = range(len(months_data))
    width = 0.35
    incomes = [d['income'] for d in months_data]
    expenses = [d['expense'] for d in months_data]

    bars1 = ax1.bar([i - width/2 for i in x], incomes, width, color='#00d4aa', label='Доходы', alpha=0.85)
    bars2 = ax1.bar([i + width/2 for i in x], expenses, width, color='#ff6b6b', label='Расходы', alpha=0.85)
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(labels, color='white', fontsize=9)
    ax1.set_title('Приход / Расход', color='white', fontsize=11, pad=8)
    ax1.legend(facecolor='#1a1a2e', labelcolor='white', fontsize=8)
    ax1.yaxis.set_tick_params(labelcolor='white')

    # Подписи на барах
    for bar in bars1:
        h = bar.get_height()
        if h > 0:
            ax1.text(bar.get_x() + bar.get_width()/2, h, '{:,.0f}'.format(h),
                    ha='center', va='bottom', color='#00d4aa', fontsize=7)
    for bar in bars2:
        h = bar.get_height()
        if h > 0:
            ax1.text(bar.get_x() + bar.get_width()/2, h, '{:,.0f}'.format(h),
                    ha='center', va='bottom', color='#ff6b6b', fontsize=7)

    # --- Середина: Расходы по категориям ---
    if cats:
        cat_names = [r[0][:15] for r in cats[:7]]
        cat_values = [float(r[1]) for r in cats[:7]]
        colors = ['#ff6b6b','#ffd93d','#6bcb77','#4d96ff','#ff922b','#cc5de8','#f06595']

        wedges, texts, autotexts = ax2.pie(
            cat_values,
            labels=cat_names,
            autopct='%1.0f%%',
            colors=colors[:len(cat_names)],
            pctdistance=0.8,
            startangle=90,
            textprops={'color': 'white', 'fontsize': 8}
        )
        for at in autotexts:
            at.set_color('white')
            at.set_fontsize(7)
        ax2.set_title('Расходы по категориям — ' + MONTHS_RU[month], color='white', fontsize=11, pad=8)
    else:
        ax2.text(0.5, 0.5, 'Нет данных', ha='center', va='center', color='white', fontsize=12)
        ax2.set_title('Расходы по категориям', color='white', fontsize=11)

    # --- Низ: Остаток (накопления) ---
    colors_bal = ['#00d4aa' if b >= 0 else '#ff6b6b' for b in balances]
    ax3.bar(labels, balances, color=colors_bal, alpha=0.85)
    ax3.axhline(y=0, color='white', linewidth=0.8, linestyle='--', alpha=0.5)
    ax3.set_title('Свободные деньги на конец месяца', color='white', fontsize=11, pad=8)
    ax3.yaxis.set_tick_params(labelcolor='white')
    ax3.set_xticklabels(labels, color='white', fontsize=9)

    for i, (val, label) in enumerate(zip(balances, labels)):
        color = '#00d4aa' if val >= 0 else '#ff6b6b'
        ax3.text(i, val + (max(balances) * 0.02 if max(balances) != 0 else 100),
                '{:,.0f}'.format(val), ha='center', va='bottom', color=color, fontsize=7)

    plt.tight_layout(pad=2.0)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight',
                facecolor='#1a1a2e', edgecolor='none')
    plt.close()
    buf.seek(0)
    return buf.read()
