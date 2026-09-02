# -*- coding: utf-8 -*-
"""
Univers PEA Europe (actions UE/EEE éligibles au Plan d'Épargne en Actions)
==========================================================================
Liste curatée de grandes capitalisations de la zone euro / EEE, éligibles au
PEA (siège dans l'UE/EEE). Tickers au format yfinance (avec suffixe de place).

Sont EXCLUS volontairement : Royaume-Uni (.L) et Suisse (.SW) — non éligibles PEA.

Ces titres sont insérés dans `IndexConstituent` avec index_name='PEA_EU', ce qui
les fait collecter automatiquement par les collecteurs existants (prix yfinance,
fondamentaux yfinance). EDGAR les ignore (pas de CIK US). Le screen fondamental
filtre l'univers par région via cet index_name.

La liste peut être étendue ; elle vise les ~110 plus grandes/liquides valeurs
éligibles, suffisantes pour un screen factoriel diversifié.
"""

# Index utilisés pour classer la région dans IndexConstituent
US_INDEX_NAMES = {'SP500', 'NDX100'}
EU_INDEX_NAMES = {'PEA_EU'}

# Univers PEA (ticker yfinance → nom). Regroupé par pays pour la lisibilité.
PEA_EU_UNIVERSE = {
    # France (.PA)
    'MC.PA': 'LVMH', 'OR.PA': "L'Oréal", 'RMS.PA': 'Hermès', 'TTE.PA': 'TotalEnergies',
    'SAN.PA': 'Sanofi', 'AIR.PA': 'Airbus', 'SU.PA': 'Schneider Electric',
    'AI.PA': 'Air Liquide', 'EL.PA': 'EssilorLuxottica', 'BNP.PA': 'BNP Paribas',
    'DG.PA': 'Vinci', 'SAF.PA': 'Safran', 'CS.PA': 'AXA', 'BN.PA': 'Danone',
    'KER.PA': 'Kering', 'ORA.PA': 'Orange', 'DSY.PA': 'Dassault Systèmes',
    'ENGI.PA': 'Engie', 'LR.PA': 'Legrand', 'PUB.PA': 'Publicis', 'GLE.PA': 'Société Générale',
    'ACA.PA': 'Crédit Agricole', 'CAP.PA': 'Capgemini', 'HO.PA': 'Thales', 'RI.PA': 'Pernod Ricard',
    'ML.PA': 'Michelin', 'VIE.PA': 'Veolia', 'SGO.PA': 'Saint-Gobain', 'STLAP.PA': 'Stellantis',
    'EN.PA': 'Bouygues', 'TEP.PA': 'Teleperformance', 'VIV.PA': 'Vivendi',
    # Allemagne (.DE)
    'SAP.DE': 'SAP', 'SIE.DE': 'Siemens', 'ALV.DE': 'Allianz', 'DTE.DE': 'Deutsche Telekom',
    'MRK.DE': 'Merck KGaA', 'MBG.DE': 'Mercedes-Benz', 'BMW.DE': 'BMW', 'VOW3.DE': 'Volkswagen',
    'BAS.DE': 'BASF', 'BAYN.DE': 'Bayer', 'ADS.DE': 'Adidas', 'DB1.DE': 'Deutsche Börse',
    'IFX.DE': 'Infineon', 'MUV2.DE': 'Munich Re', 'RWE.DE': 'RWE', 'DTG.DE': 'Daimler Truck',
    'HEN3.DE': 'Henkel', 'VNA.DE': 'Vonovia', 'EOAN.DE': 'E.ON', 'DHL.DE': 'DHL Group',
    'SHL.DE': 'Siemens Healthineers', 'CON.DE': 'Continental', 'FRE.DE': 'Fresenius',
    'SY1.DE': 'Symrise', 'BEI.DE': 'Beiersdorf', 'DBK.DE': 'Deutsche Bank',
    # Pays-Bas (.AS)
    'ASML.AS': 'ASML', 'PRX.AS': 'Prosus', 'INGA.AS': 'ING', 'AD.AS': 'Ahold Delhaize',
    'WKL.AS': 'Wolters Kluwer', 'PHIA.AS': 'Philips', 'ADYEN.AS': 'Adyen', 'HEIA.AS': 'Heineken',
    'ABN.AS': 'ABN AMRO', 'AKZA.AS': 'Akzo Nobel', 'KPN.AS': 'KPN', 'NN.AS': 'NN Group',
    'ASRNL.AS': 'ASR Nederland', 'AGN.AS': 'Aegon', 'DSFIR.AS': 'DSM-Firmenich',
    # Espagne (.MC)
    'ITX.MC': 'Inditex', 'IBE.MC': 'Iberdrola', 'SAN.MC': 'Banco Santander', 'BBVA.MC': 'BBVA',
    'TEF.MC': 'Telefónica', 'AMS.MC': 'Amadeus', 'REP.MC': 'Repsol', 'FER.MC': 'Ferrovial',
    'AENA.MC': 'Aena', 'ELE.MC': 'Endesa', 'CLNX.MC': 'Cellnex', 'RED.MC': 'Redeia',
    # Italie (.MI)
    'ENEL.MI': 'Enel', 'ISP.MI': 'Intesa Sanpaolo', 'ENI.MI': 'Eni', 'UCG.MI': 'UniCredit',
    'RACE.MI': 'Ferrari', 'G.MI': 'Generali', 'STLAM.MI': 'Stellantis (MI)', 'STM.MI': 'STMicroelectronics',
    'PRY.MI': 'Prysmian', 'MB.MI': 'Mediobanca', 'TRN.MI': 'Terna', 'SRG.MI': 'Snam',
    # Belgique (.BR)
    'ABI.BR': 'AB InBev', 'KBC.BR': 'KBC', 'UCB.BR': 'UCB', 'SOLB.BR': 'Solvay', 'GBLB.BR': 'GBL',
    # Finlande (.HE)
    'NOKIA.HE': 'Nokia', 'SAMPO.HE': 'Sampo', 'KNEBV.HE': 'Kone', 'NESTE.HE': 'Neste',
    'UPM.HE': 'UPM-Kymmene', 'FORTUM.HE': 'Fortum',
    # Irlande (.IR) / Portugal (.LS) / Autriche (.VI)
    'KRZ.IR': 'Kerry Group', 'KRX.IR': 'Kingspan',
    'EDP.LS': 'EDP', 'GALP.LS': 'Galp', 'JMT.LS': 'Jerónimo Martins',
    'OMV.VI': 'OMV', 'VER.VI': 'Verbund', 'EBS.VI': 'Erste Group',
}


def seed_pea_universe():
    """
    Insère/active l'univers PEA_EU dans IndexConstituent (idempotent).
    À appeler dans un app_context. Renvoie le nombre de titres actifs PEA_EU.
    """
    from models import db, IndexConstituent
    from datetime import datetime
    now = datetime.utcnow()
    existing = {c.ticker: c for c in
                IndexConstituent.query.filter_by(index_name='PEA_EU').all()}
    for ticker, name in PEA_EU_UNIVERSE.items():
        row = existing.get(ticker)
        if row:
            row.is_active = True
            row.last_seen_at = now
            row.name = name[:120]
        else:
            db.session.add(IndexConstituent(
                ticker=ticker, index_name='PEA_EU', name=name[:120],
                is_active=True, last_seen_at=now))
    db.session.commit()
    return IndexConstituent.query.filter_by(index_name='PEA_EU', is_active=True).count()


def universe_for_market(market='all'):
    """
    Retourne l'ensemble des tickers de l'univers pour un marché donné.
    market : 'us' | 'eu' | 'all'. Source = IndexConstituent actifs.
    """
    from models import IndexConstituent
    if market == 'us':
        names = US_INDEX_NAMES
    elif market == 'eu':
        names = EU_INDEX_NAMES
    else:
        names = US_INDEX_NAMES | EU_INDEX_NAMES
    rows = (IndexConstituent.query
            .filter(IndexConstituent.is_active.is_(True))
            .filter(IndexConstituent.index_name.in_(names)).all())
    return {r.ticker for r in rows}
