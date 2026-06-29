# -*- coding: utf-8 -*-
"""
/***************************************************************************
 VWorld Land Information Tool
                                 A QGIS plugin
 브이월드 API를 활용한 토지정보 조회 플러그인
                             -------------------
        begin                : 2024-01-01
        git sha              : $Format:%H$
        copyright            : (C) 2024 by QGIS Developer
        email                : developer@example.com
 ***************************************************************************/
"""


def classFactory(iface):
    """Load VWorldLandInfoPlugin class from file main.py

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    from .main import VWorldLandInfoPlugin
    return VWorldLandInfoPlugin(iface)
