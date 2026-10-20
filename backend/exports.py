"""Shared spreadsheet export with formula protection."""
import csv
import io

from fastapi.responses import Response
from openpyxl import Workbook

def safe_cell(value):
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return value


def export_file(rows, columns, format, name):
    values = [[safe_cell(row.get(c)) for c in columns] for row in rows]
    headers = {"Content-Disposition": f'attachment; filename="{name}.{format}"'}
    if format == "xlsx":
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("GeoProspect")
        sheet.append(columns)
        for row in values:
            sheet.append(row)
        output = io.BytesIO()
        workbook.save(output)
        return Response(output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(columns)
    writer.writerows(values)
    return Response(output.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8", headers=headers)


