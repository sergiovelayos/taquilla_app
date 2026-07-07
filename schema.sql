--
-- PostgreSQL database dump
--

\restrict bTf2K6NDJSplxoA1twX5vUpapIeBQeQScbRcEL6FGquH5tFupQsrmjklyAwRNog

-- Dumped from database version 16.11 (Homebrew)
-- Dumped by pg_dump version 16.11 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: anual_esp; Type: TABLE; Schema: public; Owner: macmini
--

CREATE TABLE public.anual_esp (
    id integer NOT NULL,
    anio integer NOT NULL,
    rank integer NOT NULL,
    titulo text NOT NULL,
    distribuidora text,
    fecha_estreno date,
    recaudacion numeric(14,2),
    espectadores integer,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.anual_esp OWNER TO macmini;

--
-- Name: anual_esp_id_seq; Type: SEQUENCE; Schema: public; Owner: macmini
--

CREATE SEQUENCE public.anual_esp_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.anual_esp_id_seq OWNER TO macmini;

--
-- Name: anual_esp_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: macmini
--

ALTER SEQUENCE public.anual_esp_id_seq OWNED BY public.anual_esp.id;


--
-- Name: icaa_fichas; Type: TABLE; Schema: public; Owner: macmini
--

CREATE TABLE public.icaa_fichas (
    expediente_icaa text NOT NULL,
    titulo text NOT NULL,
    director text,
    calificacion text,
    anio_produccion integer,
    fecha_estreno date,
    duracion_min integer,
    tipo text,
    genero text,
    nacionalidad text,
    recaudacion_eur numeric(14,2),
    espectadores integer,
    subvenciones_total_eur numeric(14,2),
    sinopsis text,
    etiquetas text[],
    ficha_artistica jsonb,
    ficha_tecnica jsonb,
    empresas_productoras text[],
    distribuidoras text[],
    subvenciones jsonb,
    fecha_inicio_rodaje date,
    fecha_fin_rodaje date,
    lugares_rodaje text[],
    premios jsonb,
    festivales jsonb,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.icaa_fichas OWNER TO macmini;

--
-- Name: processed_pdfs; Type: TABLE; Schema: public; Owner: macmini
--

CREATE TABLE public.processed_pdfs (
    id integer NOT NULL,
    filename text NOT NULL,
    report_type text NOT NULL,
    fecha_inicio date,
    fecha_fin date,
    rows_inserted integer DEFAULT 0 NOT NULL,
    processed_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.processed_pdfs OWNER TO macmini;

--
-- Name: processed_pdfs_id_seq; Type: SEQUENCE; Schema: public; Owner: macmini
--

CREATE SEQUENCE public.processed_pdfs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.processed_pdfs_id_seq OWNER TO macmini;

--
-- Name: processed_pdfs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: macmini
--

ALTER SEQUENCE public.processed_pdfs_id_seq OWNED BY public.processed_pdfs.id;


--
-- Name: subvenciones; Type: TABLE; Schema: public; Owner: macmini
--

CREATE TABLE public.subvenciones (
    id integer NOT NULL,
    titulo text,
    importe_ayuda numeric(15,2),
    presupuesto_proyecto numeric(15,2),
    tipo_ayuda text,
    anio_ayuda integer,
    expediente_icaa text,
    tmdb_id integer
);


ALTER TABLE public.subvenciones OWNER TO macmini;

--
-- Name: subvenciones_icaa_matches; Type: TABLE; Schema: public; Owner: macmini
--

CREATE TABLE public.subvenciones_icaa_matches (
    titulo_subvencion text NOT NULL,
    expediente_icaa text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.subvenciones_icaa_matches OWNER TO macmini;

--
-- Name: subvenciones_id_seq; Type: SEQUENCE; Schema: public; Owner: macmini
--

CREATE SEQUENCE public.subvenciones_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.subvenciones_id_seq OWNER TO macmini;

--
-- Name: subvenciones_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: macmini
--

ALTER SEQUENCE public.subvenciones_id_seq OWNED BY public.subvenciones.id;


--
-- Name: subvenciones_raw; Type: TABLE; Schema: public; Owner: macmini
--

CREATE TABLE public.subvenciones_raw (
    id integer NOT NULL,
    titulo text,
    importe_ayuda numeric(15,2),
    presupuesto_proyecto numeric(15,2),
    tipo_ayuda text,
    anio_ayuda integer,
    fuente text,
    imported_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.subvenciones_raw OWNER TO macmini;

--
-- Name: subvenciones_raw_id_seq; Type: SEQUENCE; Schema: public; Owner: macmini
--

CREATE SEQUENCE public.subvenciones_raw_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.subvenciones_raw_id_seq OWNER TO macmini;

--
-- Name: subvenciones_raw_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: macmini
--

ALTER SEQUENCE public.subvenciones_raw_id_seq OWNED BY public.subvenciones_raw.id;


--
-- Name: tmdb; Type: TABLE; Schema: public; Owner: macmini
--

CREATE TABLE public.tmdb (
    titulo text NOT NULL,
    distribuidora text NOT NULL,
    tmdb_id integer,
    titulo_tmdb text,
    titulo_original_tmdb text,
    tagline text,
    sinopsis text,
    duracion_min integer,
    fecha_estreno_tmdb date,
    generos text[],
    paises_produccion text[],
    productoras text[],
    director text,
    reparto_principal text[],
    keywords text[],
    puntuacion_tmdb numeric(4,2),
    votos_tmdb integer,
    popularidad_tmdb numeric(10,4),
    presupuesto_usd bigint,
    recaudacion_mundial_usd bigint,
    poster_url text,
    backdrop_url text,
    trailer_url text,
    idioma_original text,
    estado text,
    match_score numeric(4,2),
    verificado boolean DEFAULT false,
    fuentes text[],
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.tmdb OWNER TO macmini;

--
-- Name: tmdb_gente; Type: TABLE; Schema: public; Owner: macmini
--

CREATE TABLE public.tmdb_gente (
    nombre_icaa text NOT NULL,
    tmdb_id integer,
    imdb_id text,
    wikidata_id text,
    roles text[] DEFAULT '{}'::text[],
    nombre_tmdb text,
    tambien_conocido_como text[],
    foto_url text,
    foto_url_hd text,
    todas_las_fotos text[],
    biografia text,
    fecha_nacimiento date,
    lugar_nacimiento text,
    fecha_fallecimiento date,
    genero character(1),
    departamento text,
    popularidad numeric(10,4),
    homepage text,
    instagram_id text,
    twitter_id text,
    num_peliculas_director integer,
    peliculas_dirigidas text[],
    num_peliculas_actor integer,
    peliculas_actuado text[],
    match_score numeric(4,2),
    verificado boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.tmdb_gente OWNER TO macmini;

--
-- Name: top25; Type: TABLE; Schema: public; Owner: macmini
--

CREATE TABLE public.top25 (
    fecha_inicio date,
    fecha_fin date,
    rank smallint,
    titulo text,
    titulo_original text,
    distribuidora text,
    semana smallint,
    cines smallint,
    pantallas smallint,
    recaudacion numeric,
    pct_rec numeric,
    total_espectadores integer,
    pct_esp numeric,
    recaudacion_acum numeric,
    espectadores_acum integer,
    inserted_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.top25 OWNER TO macmini;

--
-- Name: topespanol; Type: TABLE; Schema: public; Owner: macmini
--

CREATE TABLE public.topespanol (
    fecha_inicio date,
    fecha_fin date,
    rank smallint,
    titulo text,
    distribuidora text,
    semana smallint,
    cines smallint,
    pantallas smallint,
    recaudacion numeric,
    pct_rec numeric,
    rec_media_cine numeric,
    rec_media_pantalla numeric,
    total_espectadores integer,
    pct_esp numeric,
    esp_media_cine numeric,
    esp_media_pantalla numeric,
    recaudacion_acum numeric,
    espectadores_acum integer,
    inserted_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.topespanol OWNER TO macmini;

--
-- Name: anual_esp id; Type: DEFAULT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.anual_esp ALTER COLUMN id SET DEFAULT nextval('public.anual_esp_id_seq'::regclass);


--
-- Name: processed_pdfs id; Type: DEFAULT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.processed_pdfs ALTER COLUMN id SET DEFAULT nextval('public.processed_pdfs_id_seq'::regclass);


--
-- Name: subvenciones id; Type: DEFAULT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.subvenciones ALTER COLUMN id SET DEFAULT nextval('public.subvenciones_id_seq'::regclass);


--
-- Name: subvenciones_raw id; Type: DEFAULT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.subvenciones_raw ALTER COLUMN id SET DEFAULT nextval('public.subvenciones_raw_id_seq'::regclass);


--
-- Name: anual_esp anual_esp_anio_rank_key; Type: CONSTRAINT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.anual_esp
    ADD CONSTRAINT anual_esp_anio_rank_key UNIQUE (anio, rank);


--
-- Name: anual_esp anual_esp_pkey; Type: CONSTRAINT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.anual_esp
    ADD CONSTRAINT anual_esp_pkey PRIMARY KEY (id);


--
-- Name: icaa_fichas icaa_fichas_pkey; Type: CONSTRAINT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.icaa_fichas
    ADD CONSTRAINT icaa_fichas_pkey PRIMARY KEY (expediente_icaa);


--
-- Name: processed_pdfs processed_pdfs_filename_key; Type: CONSTRAINT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.processed_pdfs
    ADD CONSTRAINT processed_pdfs_filename_key UNIQUE (filename);


--
-- Name: processed_pdfs processed_pdfs_pkey; Type: CONSTRAINT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.processed_pdfs
    ADD CONSTRAINT processed_pdfs_pkey PRIMARY KEY (id);


--
-- Name: subvenciones_icaa_matches subvenciones_icaa_matches_pkey; Type: CONSTRAINT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.subvenciones_icaa_matches
    ADD CONSTRAINT subvenciones_icaa_matches_pkey PRIMARY KEY (titulo_subvencion);


--
-- Name: subvenciones subvenciones_pkey; Type: CONSTRAINT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.subvenciones
    ADD CONSTRAINT subvenciones_pkey PRIMARY KEY (id);


--
-- Name: subvenciones_raw subvenciones_raw_pkey; Type: CONSTRAINT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.subvenciones_raw
    ADD CONSTRAINT subvenciones_raw_pkey PRIMARY KEY (id);


--
-- Name: tmdb_gente tmdb_gente_pkey; Type: CONSTRAINT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.tmdb_gente
    ADD CONSTRAINT tmdb_gente_pkey PRIMARY KEY (nombre_icaa);


--
-- Name: tmdb_gente tmdb_gente_tmdb_id_key; Type: CONSTRAINT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.tmdb_gente
    ADD CONSTRAINT tmdb_gente_tmdb_id_key UNIQUE (tmdb_id);


--
-- Name: tmdb tmdb_pkey; Type: CONSTRAINT; Schema: public; Owner: macmini
--

ALTER TABLE ONLY public.tmdb
    ADD CONSTRAINT tmdb_pkey PRIMARY KEY (titulo, distribuidora);


--
-- Name: anual_esp_anio_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX anual_esp_anio_idx ON public.anual_esp USING btree (anio);


--
-- Name: anual_esp_titulo_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX anual_esp_titulo_idx ON public.anual_esp USING btree (titulo);


--
-- Name: icaa_fichas_director_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX icaa_fichas_director_idx ON public.icaa_fichas USING btree (director);


--
-- Name: icaa_fichas_titulo_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX icaa_fichas_titulo_idx ON public.icaa_fichas USING btree (titulo);


--
-- Name: subvenciones_anio_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX subvenciones_anio_idx ON public.subvenciones USING btree (anio_ayuda);


--
-- Name: subvenciones_icaa_matches_expediente_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX subvenciones_icaa_matches_expediente_idx ON public.subvenciones_icaa_matches USING btree (expediente_icaa);


--
-- Name: subvenciones_titulo_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX subvenciones_titulo_idx ON public.subvenciones USING btree (titulo);


--
-- Name: tmdb_generos_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX tmdb_generos_idx ON public.tmdb USING gin (generos);


--
-- Name: tmdb_gente_tmdb_id_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX tmdb_gente_tmdb_id_idx ON public.tmdb_gente USING btree (tmdb_id);


--
-- Name: tmdb_paises_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX tmdb_paises_idx ON public.tmdb USING gin (paises_produccion);


--
-- Name: tmdb_titulo_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX tmdb_titulo_idx ON public.tmdb USING btree (titulo);


--
-- Name: tmdb_tmdb_id_idx; Type: INDEX; Schema: public; Owner: macmini
--

CREATE INDEX tmdb_tmdb_id_idx ON public.tmdb USING btree (tmdb_id);


--
-- PostgreSQL database dump complete
--

\unrestrict bTf2K6NDJSplxoA1twX5vUpapIeBQeQScbRcEL6FGquH5tFupQsrmjklyAwRNog

