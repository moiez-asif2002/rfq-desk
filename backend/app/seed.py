"""Demo catalog for a fictional industrial distributor. `uv run python -m app.seed`"""

from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Product, SalesRep

# sku, name, category, uom, price, stock, lead_time_days
PRODUCTS = [
    ("BRG-6204-2RS", "Deep Groove Ball Bearing 6204-2RS, 20x47x14mm, sealed", "bearings", "EA", "6.80", 420, 3),
    ("BRG-6205-2RS", "Deep Groove Ball Bearing 6205-2RS, 25x52x15mm, sealed", "bearings", "EA", "7.95", 310, 3),
    ("BRG-6206-ZZ", "Deep Groove Ball Bearing 6206-ZZ, 30x62x16mm, shielded", "bearings", "EA", "9.40", 150, 3),
    ("BRG-UCP205", "Pillow Block Bearing UCP205, 25mm bore, cast iron", "bearings", "EA", "18.50", 64, 5),
    ("BRG-UCF206", "4-Bolt Flange Bearing UCF206, 30mm bore", "bearings", "EA", "24.75", 40, 5),
    ("BRG-22210-E", "Spherical Roller Bearing 22210 E, 50x90x23mm", "bearings", "EA", "96.00", 12, 10),
    ("HYD-HOSE-R2-06", "Hydraulic Hose SAE 100R2AT, 3/8 in ID, 4800 psi (per ft)", "hydraulics", "FT", "4.20",
     2000, 4),
    ("HYD-HOSE-R2-08", "Hydraulic Hose SAE 100R2AT, 1/2 in ID, 4000 psi (per ft)", "hydraulics", "FT", "5.35",
     1500, 4),
    ("HYD-FTG-JIC-0606", "JIC 37 deg Male Straight Hose Fitting, 3/8 hose x 3/8 JIC", "hydraulics", "EA",
     "3.10", 900, 4),
    ("HYD-FTG-NPT-0808", "NPT Male Straight Hose Fitting, 1/2 hose x 1/2 NPT", "hydraulics", "EA", "3.45", 700, 4),
    ("HYD-CYL-2X8", "Tie-Rod Hydraulic Cylinder, 2 in bore x 8 in stroke, 3000 psi", "hydraulics", "EA",
     "189.00", 9, 14),
    ("HYD-PUMP-G10", "Hydraulic Gear Pump 10 cc/rev, SAE A 2-bolt mount", "hydraulics", "EA", "265.00", 6, 14),
    ("FST-HCS-G8-12-2", "Hex Cap Screw Grade 8, 1/2-13 x 2 in, zinc yellow (box of 50)", "fasteners", "BOX",
     "38.00", 80, 2),
    ("FST-HCS-G5-38-1", "Hex Cap Screw Grade 5, 3/8-16 x 1 in, zinc (box of 100)", "fasteners", "BOX", "19.00",
     120, 2),
    ("FST-NUT-G8-12", "Hex Nut Grade 8, 1/2-13, zinc yellow (box of 100)", "fasteners", "BOX", "14.00", 95, 2),
    ("FST-WSH-FL-12", "Flat Washer SAE, 1/2 in, hardened (box of 100)", "fasteners", "BOX", "9.50", 150, 2),
    ("FST-SHCS-316-M8-30", "Socket Head Cap Screw 316 Stainless, M8 x 30mm (box of 50)", "fasteners", "BOX",
     "42.00", 35, 7),
    ("PNU-VLV-52-14-24D", "5/2 Solenoid Valve, 1/4 in NPT, 24VDC coil", "pneumatics", "EA", "78.00", 45, 5),
    ("PNU-CYL-32-100", "ISO 15552 Pneumatic Cylinder, 32mm bore x 100mm stroke", "pneumatics", "EA", "112.00",
     22, 7),
    ("PNU-FRL-14", "Filter Regulator Lubricator Unit, 1/4 in NPT", "pneumatics", "EA", "64.00", 30, 5),
    ("PNU-TUBE-PU-08", "Polyurethane Pneumatic Tubing, 8mm OD (per meter)", "pneumatics", "M", "1.10", 3000, 3),
    ("PNU-FTG-PTC-08-14", "Push-to-Connect Male Fitting, 8mm tube x 1/4 NPT", "pneumatics", "EA", "2.40", 800, 3),
    ("PT-VBELT-B48", "Classical V-Belt B48", "power_transmission", "EA", "12.40", 60, 3),
    ("PT-VBELT-B52", "Classical V-Belt B52", "power_transmission", "EA", "13.10", 55, 3),
    ("PT-CHAIN-40-10", "ANSI #40 Roller Chain, 10 ft box", "power_transmission", "EA", "36.00", 40, 3),
    ("PT-SPKT-40B18-075", "Sprocket #40, 18 teeth, 3/4 in bore", "power_transmission", "EA", "22.00", 28, 5),
    ("MTR-3PH-5HP-184T", "3-Phase TEFC Electric Motor, 5 HP, 1800 RPM, 184T frame, 230/460V", "motors", "EA",
     "620.00", 4, 10),
    ("MTR-3PH-2HP-145T", "3-Phase TEFC Electric Motor, 2 HP, 1800 RPM, 145T frame, 230/460V", "motors", "EA",
     "410.00", 7, 10),
    ("MTR-VFD-5HP-460", "Variable Frequency Drive, 5 HP, 460V 3-phase input", "motors", "EA", "540.00", 5, 12),
    ("SAF-GLV-CUT-A4-L", "Cut Resistant Gloves ANSI A4, Large (pair)", "safety", "PR", "7.90", 600, 3),
    ("SAF-GLV-CUT-A4-M", "Cut Resistant Gloves ANSI A4, Medium (pair)", "safety", "PR", "7.90", 540, 3),
    ("SAF-GLS-CLR-AF", "Safety Glasses, clear anti-fog lens, ANSI Z87.1", "safety", "EA", "3.20", 900, 3),
    ("SAF-EAR-FOAM-200", "Foam Ear Plugs NRR 32 (box of 200 pairs)", "safety", "BOX", "24.00", 70, 3),
]

REPS = [
    ("Sara Khan", "sara.khan@acme-industrial.example", ["bearings", "power_transmission", "motors"]),
    ("Daniel Ortiz", "daniel.ortiz@acme-industrial.example", ["hydraulics", "pneumatics"]),
    ("Priya Nair", "priya.nair@acme-industrial.example", ["fasteners", "safety"]),
]


def seed(session: Session) -> None:
    for sku, name, category, uom, price, stock, lead in PRODUCTS:
        values = dict(sku=sku, name=name, category=category, uom=uom, unit_price=Decimal(price),
                      stock_qty=stock, lead_time_days=lead)
        session.execute(
            insert(Product).values(**values).on_conflict_do_update(index_elements=["sku"], set_=values)
        )
    for name, email, categories in REPS:
        values = dict(name=name, email=email, categories=categories)
        session.execute(
            insert(SalesRep).values(**values).on_conflict_do_update(index_elements=["email"], set_=values)
        )
    session.commit()


if __name__ == "__main__":
    from app.db import get_sessionmaker, init_db

    init_db()
    with get_sessionmaker()() as s:
        seed(s)
    print(f"Seeded {len(PRODUCTS)} products and {len(REPS)} sales reps.")
