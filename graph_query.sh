#!/bin/bash
# استعلام الـ CodeGraph وتجهيز النتيجة لـ Claude.
# الاستخدام:  ./graph_query.sh <اسم_الدالة_أو_جزء_منه>
# يكتب العلاقات (من يستدعي / ماذا يَستدعي) إلى .graph_context
#
# ملاحظات التصحيح مقابل النسخة الأصلية:
#   • المسار الصحيح لقاعدة البيانات هو .codegraph/codegraph.db (وليس codegraph.db)
#   • لا يوجد جدول calls — الاستدعاءات مخزّنة في edges(kind='calls') بمعرّفات عُقد،
#     فنربطها بجدول nodes مرّتين للحصول على الأسماء + الملف + السطر
#   • نستخدم python لأن أداة sqlite3 غير مثبّتة في الصدفة

QUERY="$1"
if [ -z "$QUERY" ]; then
  echo "الاستخدام: ./graph_query.sh <اسم_الدالة>"
  exit 1
fi

DB=".codegraph/codegraph.db"
if [ ! -f "$DB" ]; then
  echo "خطأ: لم يُعثر على قاعدة البيانات في $DB — شغّل codegraph init أولاً."
  exit 1
fi

python - "$QUERY" <<'PY'
import sqlite3, sys
q = sys.argv[1]
con = sqlite3.connect(".codegraph/codegraph.db")
cur = con.cursor()
like = f"%{q}%"

sql = """
SELECT src.name        AS caller,
       dst.name        AS callee,
       e.kind          AS rel,
       src.file_path   AS file,
       e.line          AS line
FROM edges e
JOIN nodes src ON src.id = e.source
JOIN nodes dst ON dst.id = e.target
WHERE e.kind = 'calls'
  AND (src.name LIKE ? OR dst.name LIKE ?)
ORDER BY src.file_path, e.line
"""
rows = cur.execute(sql, (like, like)).fetchall()
lines = []
if not rows:
    lines.append(f"لا توجد علاقات استدعاء مطابقة لـ '{q}'.")
else:
    lines.append(f"# علاقات الاستدعاء المتعلقة بـ '{q}'  (العدد: {len(rows)})")
    lines.append(f"{'CALLER':<32} {'CALLEE':<32} {'FILE':<40} LINE")
    for caller, callee, rel, file, line in rows:
        lines.append(f"{(caller or '?'):<32} {(callee or '?'):<32} {(file or '?'):<40} {line or ''}")
con.close()
# اكتب بترميز UTF-8 صراحةً (Windows يستخدم cp1252 افتراضياً ويفشل مع العربية)
with open(".graph_context", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
PY

echo "تم استخراج العلاقات المتعلقة بـ \"$QUERY\" في ملف .graph_context"
