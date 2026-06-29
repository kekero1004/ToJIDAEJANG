# -*- coding: utf-8 -*-
"""
AI 규제 분석 모듈
- OpenAI GPT-4o / Anthropic Claude 지원
- 용도지역 기반 규제 분석
- 건축 가능 건축물 가이드
- 입지 총평 생성
"""

import json
import ssl
import urllib.request
import urllib.parse

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout,
    QLabel, QPushButton, QComboBox, QTextEdit, QMessageBox
)
from qgis.core import Qgis, QgsMessageLog


class AIAnalyzer:
    """AI 규제 분석 클래스"""

    def __init__(self, provider='openai', api_key=''):
        self.provider = provider  # 'openai' or 'anthropic'
        self.api_key = api_key
        self.debug_log = []
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def set_provider(self, provider):
        self.provider = provider

    def set_api_key(self, api_key):
        self.api_key = api_key

    def analyze_regulations(self, land_info):
        """토지 정보 기반 AI 규제 분석"""
        if not self.api_key:
            return "AI API 키가 설정되지 않았습니다."

        prompt = self._build_prompt(land_info)

        if self.provider == 'openai':
            return self._call_openai(prompt)
        elif self.provider == 'anthropic':
            return self._call_anthropic(prompt)
        else:
            return "지원하지 않는 AI 제공자입니다."

    def _build_prompt(self, land_info):
        """분석 프롬프트 구성"""
        use_plan = land_info.get('land_use_plan', [])
        cadastral = land_info.get('cadastral', [])
        building = land_info.get('building_register', [])
        terrain = land_info.get('terrain', {})

        # 용도지역 정보
        use_areas = []
        for item in use_plan:
            props = item.get('properties', {})
            area_nm = props.get('prpos_area_dstrc_nm', '')
            plan_nm = props.get('cty_plan_spfc_nm', '')
            if area_nm:
                use_areas.append(area_nm)
            if plan_nm:
                use_areas.append(plan_nm)

        use_area_text = ', '.join(set(use_areas)) if use_areas else '정보 없음'

        # 필지 수 및 면적
        parcel_count = len(cadastral)

        # 지목 분포
        jimok_dist = {}
        for item in cadastral:
            props = item.get('properties', {})
            jibun = props.get('jibun', '')
            if jibun and jibun[-1] in '전답대임':
                jimok = jibun[-1]
                jimok_dist[jimok] = jimok_dist.get(jimok, 0) + 1

        jimok_text = ', '.join([f"{k}: {v}필지" for k, v in jimok_dist.items()]) if jimok_dist else '정보 없음'

        # 건축물 정보
        building_info = ""
        if building:
            bld_count = len(building)
            building_info = f"건축물 수: {bld_count}동"

        # 지형 정보
        terrain_info = ""
        if terrain:
            avg_elev = terrain.get('avg_elevation', 0)
            avg_slope = terrain.get('avg_slope', 0)
            terrain_info = f"평균 표고: {avg_elev:.1f}m, 평균 경사도: {avg_slope:.1f}도"

        prompt = f"""다음 토지 정보를 기반으로 규제 분석을 수행해 주세요.

[토지 정보]
- 용도지역/지구: {use_area_text}
- 필지 수: {parcel_count}
- 지목 분포: {jimok_text}
- {building_info}
- {terrain_info}

다음 항목을 분석해 주세요:
1. **용도지역 규제 요약**: 해당 용도지역에서의 주요 규제 사항
2. **건축 가능 건축물**: 해당 용도지역에서 건축 가능한 건축물 종류
3. **건폐율/용적률 가이드**: 적용되는 건폐율 및 용적률 기준
4. **개발 시 유의사항**: 개발 진행 시 주의해야 할 법규 및 인허가 사항
5. **입지 총평**: 개발 가능성, 투자 가치, 활용도에 대한 종합 평가

한국 국토계획법, 건축법 기준으로 분석해 주세요.
"""
        return prompt

    def _call_openai(self, prompt):
        """OpenAI GPT-4o API 호출"""
        try:
            url = "https://api.openai.com/v1/chat/completions"

            payload = json.dumps({
                'model': 'gpt-4o',
                'messages': [
                    {'role': 'system', 'content': '당신은 한국 부동산 및 토지 규제 전문가입니다. 국토계획법, 건축법 등을 기반으로 정확한 규제 분석을 제공합니다.'},
                    {'role': 'user', 'content': prompt}
                ],
                'temperature': 0.3,
                'max_tokens': 2000
            }).encode('utf-8')

            req = urllib.request.Request(url)
            req.add_header('Content-Type', 'application/json')
            req.add_header('Authorization', f'Bearer {self.api_key}')

            with urllib.request.urlopen(req, data=payload, timeout=60, context=self.ssl_context) as response:
                data = json.loads(response.read().decode('utf-8'))
                if 'choices' in data and data['choices']:
                    return data['choices'][0]['message']['content']
                return "API 응답에서 결과를 찾을 수 없습니다."

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode('utf-8')
            except:
                pass
            self.debug_log.append(f"OpenAI API Error: {e.code} - {error_body[:500]}")
            return f"OpenAI API 오류 ({e.code}): API 키를 확인하세요."
        except Exception as e:
            self.debug_log.append(f"OpenAI Error: {e}")
            return f"OpenAI API 호출 오류: {e}"

    def _call_anthropic(self, prompt):
        """Anthropic Claude API 호출"""
        try:
            url = "https://api.anthropic.com/v1/messages"

            payload = json.dumps({
                'model': 'claude-sonnet-4-20250514',
                'max_tokens': 2000,
                'messages': [
                    {'role': 'user', 'content': prompt}
                ],
                'system': '당신은 한국 부동산 및 토지 규제 전문가입니다. 국토계획법, 건축법 등을 기반으로 정확한 규제 분석을 제공합니다.'
            }).encode('utf-8')

            req = urllib.request.Request(url)
            req.add_header('Content-Type', 'application/json')
            req.add_header('x-api-key', self.api_key)
            req.add_header('anthropic-version', '2023-06-01')

            with urllib.request.urlopen(req, data=payload, timeout=60, context=self.ssl_context) as response:
                data = json.loads(response.read().decode('utf-8'))
                if 'content' in data and data['content']:
                    return data['content'][0].get('text', '')
                return "API 응답에서 결과를 찾을 수 없습니다."

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode('utf-8')
            except:
                pass
            self.debug_log.append(f"Anthropic API Error: {e.code} - {error_body[:500]}")
            return f"Claude API 오류 ({e.code}): API 키를 확인하세요."
        except Exception as e:
            self.debug_log.append(f"Anthropic Error: {e}")
            return f"Claude API 호출 오류: {e}"


class AIAnalysisTab(QWidget):
    """AI 분석 탭 위젯"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ai_analyzer = None
        self.land_info = {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # AI 설정
        settings_group = QGroupBox("AI 분석 설정")
        settings_layout = QGridLayout()

        settings_layout.addWidget(QLabel("AI 제공자:"), 0, 0)
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["OpenAI (GPT-4o)", "Anthropic (Claude)"])
        settings_layout.addWidget(self.provider_combo, 0, 1)

        settings_layout.addWidget(QLabel("API 키:"), 0, 2)
        from qgis.PyQt.QtWidgets import QLineEdit
        self.ai_api_key_edit = QLineEdit()
        self.ai_api_key_edit.setEchoMode(QLineEdit.Password)
        self.ai_api_key_edit.setPlaceholderText("AI API 키 입력...")
        settings_layout.addWidget(self.ai_api_key_edit, 0, 3)

        self.analyze_btn = QPushButton("AI 분석 실행")
        self.analyze_btn.clicked.connect(self.run_analysis)
        settings_layout.addWidget(self.analyze_btn, 0, 4)

        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)

        # 분석 결과
        result_group = QGroupBox("AI 규제 분석 결과")
        result_layout = QVBoxLayout()

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText(
            "AI 분석을 실행하면 여기에 결과가 표시됩니다.\n\n"
            "분석 항목:\n"
            "1. 용도지역 규제 요약\n"
            "2. 건축 가능 건축물 종류\n"
            "3. 건폐율/용적률 가이드\n"
            "4. 개발 시 유의사항\n"
            "5. 입지 총평"
        )
        result_layout.addWidget(self.result_text)

        result_group.setLayout(result_layout)
        layout.addWidget(result_group)

    def set_land_info(self, land_info):
        """토지 정보 설정"""
        self.land_info = land_info

    def run_analysis(self):
        """AI 분석 실행"""
        api_key = self.ai_api_key_edit.text().strip()
        if not api_key:
            QMessageBox.warning(self, "경고", "AI API 키를 입력하세요.")
            return

        if not self.land_info:
            QMessageBox.warning(self, "경고", "먼저 토지 정보를 조회하세요.")
            return

        provider = 'openai' if self.provider_combo.currentIndex() == 0 else 'anthropic'

        self.ai_analyzer = AIAnalyzer(provider=provider, api_key=api_key)

        self.result_text.setText("AI 분석 중... 잠시 기다려 주세요.")
        self.analyze_btn.setEnabled(False)

        from qgis.PyQt.QtWidgets import QApplication
        QApplication.processEvents()

        try:
            result = self.ai_analyzer.analyze_regulations(self.land_info)
            self.result_text.setText(result)
        except Exception as e:
            self.result_text.setText(f"AI 분석 오류: {e}")
        finally:
            self.analyze_btn.setEnabled(True)

    def get_report_data(self):
        """보고서 내보내기용 섹션 (export_manager 공통 형식)"""
        text = self.result_text.toPlainText().strip()
        if not text or text.startswith("AI 분석 중") or \
                text.startswith("AI 분석 오류"):
            return None
        return {'title': 'AI 규제·입지 분석', 'text': text}
